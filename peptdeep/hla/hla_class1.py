import os
import torch
import pandas as pd
import tqdm

from typing import Union

import peptdeep.model.building_block as building_block
from peptdeep.model.model_interface import ModelInterface
from peptdeep.model.featurize import get_ascii_indices
from peptdeep.pretrained_models import (
    pretrain_dir, download_models, global_settings
)

from .hla_utils import (
    get_random_sequences,
    load_prot_df,
    cat_proteins,
    get_seq_series,
    nonspecific_digest_cat_proteins,
)


_model_zip_name = global_settings['local_hla_model_zip_name']
_model_url = global_settings['hla_model_url']
_model_zip = os.path.join(
    pretrain_dir, _model_zip_name
)

if not os.path.exists(_model_zip):
    download_models(url=_model_url, target_path=_model_zip)

class HLA_Class_I_LSTM(torch.nn.Module):
    def __init__(self, *,
        hidden_dim=256,
        input_dim=128,
        n_lstm_layers=4,
        dropout=0.1,
    ):
        super().__init__()
        self.dropout = torch.nn.Dropout(dropout)

        self.nn = torch.nn.Sequential(
            torch.nn.Embedding(input_dim, hidden_dim//4),
            building_block.SeqCNN(hidden_dim//4),
            self.dropout,
            building_block.SeqLSTM(hidden_dim, hidden_dim, rnn_layer=n_lstm_layers),
            building_block.SeqAttentionSum(hidden_dim),
            self.dropout,
            torch.nn.Linear(hidden_dim,64),
            torch.nn.GELU(),
            torch.nn.Linear(64, 1),
            torch.nn.Sigmoid()
        )
    def forward(self, x):
        return self.nn(x).squeeze(-1)

class HLA_Class_I_Bert(torch.nn.Module):
    def __init__(self,
        nlayers = 4,
        input_dim = 128,
        hidden_dim = 256,
        output_attentions=False,
        dropout = 0.1,
        **kwargs,
    ):
        """
        Model based on a transformer Architecture from
        Huggingface's BertEncoder class.
        """
        super().__init__()

        self.dropout = torch.nn.Dropout(dropout)

        self.input_nn =  torch.nn.Sequential(
            torch.nn.Embedding(input_dim, hidden_dim),
            building_block.PositionalEncoding(hidden_dim)
        )

        self._output_attentions = output_attentions

        self.hidden_nn = building_block.Hidden_HFace_Transformer(
            hidden_dim, nlayers=nlayers, dropout=dropout,
            output_attentions=output_attentions
        )

        self.output_nn = torch.nn.Sequential(
            building_block.SeqAttentionSum(hidden_dim),
            torch.nn.PReLU(),
            self.dropout,
            torch.nn.Linear(hidden_dim, 1),
            torch.nn.Sigmoid()
        )

    @property
    def output_attentions(self):
        return self._output_attentions

    @output_attentions.setter
    def output_attentions(self, val:bool):
        self._output_attentions = val
        self.hidden_nn.output_attentions = val

    def forward(self, x):
        x = self.dropout(self.input_nn(x))

        x = self.hidden_nn(x)
        if self.output_attentions:
            self.attentions = x[1]
        else:
            self.attentions = None
        x = self.dropout(x[0])

        return self.output_nn(x).squeeze(1)

class HLA1_Binding_Classifier(ModelInterface):
    """
    Class to predict HLA-binding probabilities of peptides.
    """
    def __init__(self,
        dropout:float=0.1,
        model_class:type=HLA_Class_I_LSTM, # model defined above
        device:str='gpu',
        min_peptide_length:int=8,
        max_peptide_length:int=14,
        **kwargs,
    ):
        """
        Parameters
        ----------
        dropout : float, optional
            dropout rate of the model, by default 0.1
        model_class : torch.nn.Module, optional
            The model class type, can be :class:`HLA_Class_I_LSTM` or
            :class:`HLA_Class_I_Bert`, by default :class:`HLA_Class_I_LSTM`
        min_peptide_length : int, optional
            minimal peptide length after digestion, by default 8
        max_peptide_length : int, optional
            maximal peptide length after digestion, by default 14
        """
        super().__init__(device=device)
        self.build(
            model_class,
            dropout=dropout,
            **kwargs
        )
        self.loss_func = torch.nn.BCELoss()
        self.target_column_to_predict = 'HLA_prob_pred'
        self.min_peptide_length = min_peptide_length
        self.max_peptide_length = max_peptide_length
        self._n_neg_per_pos_training = 1

        self.predict_batch_size = 4096

    def _prepare_predict_data_df(self,
        precursor_df:pd.DataFrame,
    ):
        self.__training = False
        precursor_df[self.target_column_to_predict] = 0.
        self.predict_df = precursor_df

    def _prepare_train_data_df(self,
        precursor_df, **kwargs
    ):
        self.__training = True
        precursor_df['nAA'] = precursor_df.sequence.str.len()
        precursor_df.drop(
            index=precursor_df[
                (precursor_df.nAA<8)|(precursor_df.nAA>14)
            ].index,
            inplace=True,
        )
        precursor_df.reset_index(inplace=True,drop=True)

    def _get_features_from_batch_df(self,
        batch_df: pd.DataFrame,
        **kwargs,
    ):
        aa_indices = self._as_tensor(
            get_ascii_indices(
                batch_df['sequence'].values.astype('U')
            ), dtype=torch.long
        )

        if self.__training:
            rnd_seqs = get_random_sequences(
                self.protein_df,
                n=int(len(batch_df)*self._n_neg_per_pos_training),
                pep_len = batch_df.nAA.values[0]
            )
            aa_indices = torch.cat(
                [aa_indices,
                 self._as_tensor(
                     get_ascii_indices(rnd_seqs), dtype=torch.long
                 )
                ], axis=0
            )

        return aa_indices

    def _get_targets_from_batch_df(self,
        batch_df: pd.DataFrame,
        **kwargs
    ) -> torch.Tensor:
        x = torch.zeros(
            len(batch_df)+(
                int(len(batch_df)*self._n_neg_per_pos_training)
                if self.__training else 0
            ),
            device=self.device
        )
        x[:len(batch_df)] = 1
        return x

    def load_proteins(self,
        protein_data:Union[str,list,dict],
    ):
        """
        Load proteins, and generate :attr:`protein_df` and
        :attr:`_cat_protein_sequence` in this object.

        Parameters
        ----------
        protein_data : Union[str,list,dict]
            str: a fasta file
            list: a fasta file list
            dict: protein_dict
        """
        self.protein_df = load_prot_df(
            protein_data
        )
        self._cat_protein_sequence = cat_proteins(
            self.protein_df["sequence"].to_numpy()
        )

    def digest_proteins(self):
        """
        Unspecific digestion of proteins generates :attr:`digested_idxes_df`.
        """
        self.digested_idxes_df = nonspecific_digest_cat_proteins(
            self._cat_protein_sequence,
            self.min_peptide_length,
            self.max_peptide_length
        )

    def _predict_all_probs(self, digest_batch_size):
        for i in tqdm.tqdm(range(
            0, len(self.digested_idxes_df),
            digest_batch_size
        )):
            _df = self.digested_idxes_df.iloc[i:i+digest_batch_size]
            seq_df = get_seq_series(
                _df, self._cat_protein_sequence
            ).to_frame('sequence')
            seq_df['nAA'] = _df.nAA
            self.predict(seq_df,
                batch_size=self.predict_batch_size
            )
            self.digested_idxes_df[
                self.target_column_to_predict
            ].values[i:i+digest_batch_size] = seq_df[
                self.target_column_to_predict
            ]

    def predict_peptide_df_(self,
        peptide_df:pd.DataFrame,
    ):
        """
        Predict HLA probabilities for the given peptide dataframe
        Probabilities are predicted inplace in `peptide_df` with
        the predicted `HLA_prob_pred` column.

        Parameters
        ----------
        peptide_df : pd.DataFrame
            peptide dataframe with `sequence` column.
        """
        peptide_df = self.predict(
            peptide_df,
            batch_size=self.predict_batch_size
        )

    def predict_from_proteins(self,
        protein_data: Union[pd.DataFrame, str, list, dict],
        prob_threshold:float=0.7,
        digest_batch_size:int=1024000,
    )->pd.DataFrame:
        """
        Digest peptides from :attr:`protein_df`.

        Parameters
        ----------
        protein_data : pd.DataFrame | str | list | dict
            pd.DataFrame: protein_df with a `sequence` column
            str : absolute or relative fasta file path
            list: list of fasta file path
            dict: protein dict structure
        prob_threshold : float, optional
            Peptides above this probability are kept, by default 0.7
        digest_batch_size : int, optional
            Batch size for digestion, by default 1024000

        Returns
        -------
        pd.DataFrame
            The peptide dataframe in alphabase format.
        """
        if isinstance(protein_data, pd.DataFrame):
            self.protein_df = protein_data
            self._cat_protein_sequence = cat_proteins(
                self.protein_df["sequence"].to_numpy()
            )
        else:
            self.load_proteins(protein_data=protein_data)

        self.digest_proteins()
        self.digested_idxes_df[
            self.target_column_to_predict
        ] = 0.0

        self._predict_all_probs(digest_batch_size)

        peptide_df = self.digested_idxes_df[
            self.digested_idxes_df[
                self.target_column_to_predict
            ]>=prob_threshold
        ].reset_index(drop=True)

        peptide_df['sequence'] = get_seq_series(
            peptide_df,
            self._cat_protein_sequence
        )
        return peptide_df

    def load_pretrained_hla_model(self):
        """
        Load pretrained `HLA1_IEDB.pt` model.
        """
        self.load(
            model_file=_model_zip,
            model_path_in_zip="HLA1_IEDB.pt"
        )
