# AUTOGENERATED! DO NOT EDIT! File to edit: nbdev_nbs/model/base.ipynb (unless otherwise specified).

__all__ = ['ModelImplBase', 'zero_param', 'xavier_param', 'aa_embedding', 'init_state', 'SeqCNN', 'SeqLSTM', 'SeqGRU',
           'SeqTransformer', 'SeqAttentionSum', 'LinearDecoder', 'SeqEncoder', 'SeqDecoder']

# Cell
import os
import numpy as np
import pandas as pd
import torch
from tqdm import tqdm

from zipfile import ZipFile
from typing import IO, Tuple, List, Union
from alphabase.yaml_utils import save_yaml
from alphadeep._settings import model_const

# Cell
class ModelImplBase(object):
    def __init__(self, *args, **kwargs):
        if 'GPU' in kwargs:
            self.use_GPU(kwargs['GPU'])
        else:
            self.use_GPU(True)

    def use_GPU(self, GPU=True):
        if not torch.cuda.is_available():
            GPU=False
        self.device = torch.device('cuda' if GPU else 'cpu')

    def init_train(self, lr=0.001):
        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=lr)
        self.loss_func = torch.nn.L1Loss()

    def build(self,
        model_class: torch.nn.Module,
        lr = 0.001,
        **kwargs
    ):
        self.model = model_class(**kwargs)
        self.model.to(self.device)
        self.init_train(lr)

    def get_parameter_num(self):
        return np.sum([p.numel() for p in self.model.parameters()])

    def save(self, save_as):
        dir = os.path.dirname(save_as)
        if not dir: dir = './'
        if not os.path.exists(dir): os.makedirs(dir)
        torch.save(self.model.state_dict(), save_as)
        with open(save_as+'.txt','w') as f: f.write(str(self.model))
        save_yaml(save_as+'.model_const.yaml', model_const)

    def _load_model_file(self, stream):
        self.model.load_state_dict(torch.load(
            stream, map_location=self.device)
        )

    def load(
        self,
        model_file: Tuple[str, IO],
        model_name_in_zip: str = None,
        **kwargs
    ):
        if isinstance(model_file, str):
            # We may release all models (msms, rt, ccs, ...) in a single zip file
            if model_file.lower().endswith('.zip'):
                with ZipFile(model_file, 'rb') as model_zip:
                    with model_zip.open(model_name_in_zip) as pt_file:
                        self._load_model_file(pt_file)
            else:
                with open(model_file,'rb') as pt_file:
                    self._load_model_file(pt_file)
        else:
            self._load_model_file(model_file)

    def _train_one_batch(
        self,
        targets:Union[torch.Tensor,List[torch.Tensor]],
        *features,
    ):
        self.optimizer.zero_grad()
        predicts = self.model(*[fea.to(self.device) for fea in features])
        if isinstance(targets, list):
            # predicts must be a list or tuple as well
            cost = self.loss_func(
                predicts,
                [t.to(self.device) for t in targets]
            )
        else:
            cost = self.loss_func(predicts, targets.to(self.device))
        cost.backward()
        self.optimizer.step()
        return cost.item()

    def _predict_one_batch(self,
        *features
    ):
        predicts = self.model(*[fea.to(self.device) for fea in features])
        if isinstance(predicts, torch.Tensor):
            return predicts.cpu().detach().numpy()
        else:
            return [p.cpu().detach().numpy() for p in predicts]

    def _get_targets_from_batch_df(self,
        batch_df:pd.DataFrame,
        nAA, **kwargs,
    )->Union[torch.Tensor,List]:
        raise NotImplementedError(
            'Must implement _get_targets_from_batch_df() method'
        )

    def _get_features_from_batch_df(self,
        batch_df:pd.DataFrame,
        nAA, **kwargs,
    )->Tuple[torch.Tensor]:
        raise NotImplementedError(
            'Must implement _get_features_from_batch_df() method'
        )

    def _prepare_predict_data_df(self,
        precursor_df:pd.DataFrame,
        **kwargs
    ):
        '''
        This method must create a `self.predict_df` dataframe.
        '''
        self.predict_df = pd.DataFrame()

    def _prepare_train_data_df(self,
        precursor_df:pd.DataFrame,
        **kwargs
    ):
        pass

    def _set_batch_predict_data(self,
        batch_df:pd.DataFrame,
        predicts:Union[torch.Tensor, List],
        **kwargs
    ):
        raise NotImplementedError(
            'Must implement _set_batch_predict_data_df() method'
        )

    def train(self,
        precursor_df: pd.DataFrame,
        batch_size=1024,
        epoch=20,
        verbose=False,
        verbose_each_epoch=True,
        **kwargs
    ):
        self._prepare_train_data_df(precursor_df, **kwargs)
        self.model.train()

        for epoch in range(epoch):
            batch_cost = []
            _grouped = list(precursor_df.sample(frac=1).groupby('nAA'))
            rnd_nAA = np.random.permutation(len(_grouped))
            if verbose_each_epoch:
                batch_tqdm = tqdm(rnd_nAA)
            else:
                batch_tqdm = rnd_nAA
            for i_group in batch_tqdm:
                nAA, df_group = _grouped[i_group]
                df_group = df_group.reset_index(drop=True)
                for i in range(0, len(df_group), batch_size):
                    batch_end = i+batch_size-1 # DataFrame.loc[start:end] inlcudes the end

                    batch_df = df_group.loc[i:batch_end,:]
                    targets = self._get_targets_from_batch_df(batch_df,nAA,**kwargs)
                    features = self._get_features_from_batch_df(batch_df,nAA,**kwargs)

                    cost = self._train_one_batch(
                        targets,
                        *features,
                    )
                    batch_cost.append(cost)
                if verbose_each_epoch:
                    batch_tqdm.set_description(
                        f'Epoch={epoch+1}, nAA={nAA}, Batch={len(batch_cost)}, Loss={cost:.4f}'
                    )
            if verbose: print(f'[Training] Epoch={epoch+1}, Mean Loss={np.mean(batch_cost)}')

        torch.cuda.empty_cache()

    def predict(self,
        precursor_df:pd.DataFrame,
        batch_size=1024,
        verbose=False,**kwargs
    )->pd.DataFrame:
        self._prepare_predict_data_df(precursor_df,**kwargs)
        self.model.eval()

        _grouped = precursor_df.groupby('nAA')
        if verbose:
            batch_tqdm = tqdm(_grouped)
        else:
            batch_tqdm = _grouped

        for nAA, df_group in batch_tqdm:
            for i in range(0, len(df_group), batch_size):
                batch_end = i+batch_size

                batch_df = df_group.iloc[i:batch_end,:]

                features = self._get_features_from_batch_df(
                    batch_df, nAA, **kwargs
                )

                predicts = self._predict_one_batch(*features)

                self._set_batch_predict_data(
                    batch_df, predicts,
                    **kwargs
                )

        torch.cuda.empty_cache()
        return self.predict_df

# Cell
def zero_param(*shape):
    return torch.nn.Parameter(torch.zeros(shape), requires_grad=False)

def xavier_param(*shape):
    x = torch.nn.Parameter(torch.empty(shape), requires_grad=False)
    torch.nn.init.xavier_uniform_(x)
    return x

init_state = xavier_param

def aa_embedding(embedding_size):
    return torch.nn.Embedding(27, embedding_size, padding_idx=0)

# Cell
class SeqCNN(torch.nn.Module):
    def __init__(self, embedding_hidden):
        super().__init__()

        self.cnn_short = torch.nn.Conv1d(
            embedding_hidden, embedding_hidden,
            kernel_size=3, padding=1
        )
        self.cnn_medium = torch.nn.Conv1d(
            embedding_hidden, embedding_hidden,
            kernel_size=5, padding=2
        )
        self.cnn_long = torch.nn.Conv1d(
            embedding_hidden, embedding_hidden,
            kernel_size=7, padding=3
        )

    def forward(self, x):
        x = x.transpose(1, 2)
        x1 = self.cnn_short(x)
        x2 = self.cnn_medium(x)
        x3 = self.cnn_long(x)
        return torch.cat((x, x1, x2, x3), dim=1).transpose(1,2)

# Cell
class SeqLSTM(torch.nn.Module):
    def __init__(self, in_features, out_features,
                 rnn_layer=2, bidirectional=True
        ):
        super().__init__()

        if bidirectional:
            hidden = out_features//2
        else:
            hidden = out_features

        self.rnn_h0 = init_state(
            rnn_layer+rnn_layer*bidirectional,
            1, hidden
        )
        self.rnn_c0 = init_state(
            rnn_layer+rnn_layer*bidirectional,
            1, hidden
        )
        self.rnn = torch.nn.LSTM(
            input_size = in_features,
            hidden_size = hidden,
            num_layers = rnn_layer,
            batch_first = True,
            bidirectional = bidirectional,
        )

    def forward(self, x:torch.Tensor):
        h0 = self.rnn_h0.repeat(1, x.size(0), 1)
        c0 = self.rnn_c0.repeat(1, x.size(0), 1)
        x, _ = self.rnn(x, (h0,c0))
        return x


class SeqGRU(torch.nn.Module):
    def __init__(self, in_features, out_features,
                 rnn_layer=2, bidirectional=True
        ):
        super().__init__()

        if bidirectional:
            hidden = out_features//2
        else:
            hidden = out_features

        self.rnn_h0 = init_state(
            rnn_layer+rnn_layer*bidirectional,
            1, hidden
        )
        self.rnn = torch.nn.GRU(
            input_size = in_features,
            hidden_size = hidden,
            num_layers = rnn_layer,
            batch_first = True,
            bidirectional = bidirectional,
        )

    def forward(self, x:torch.Tensor):
        h0 = self.rnn_h0.repeat(1, x.size(0), 1)
        x, _ = self.rnn(x, h0)
        return x

# Cell
class SeqTransformer(torch.nn.Module):
    def __init__(self,
        in_features,
        out_features,
        nhead=8,
        nlayers=2,
        dropout=0.2
    ):
        super().__init__()
        encoder_layers = torch.nn.TransformerEncoderLayer(
            in_features, nhead, out_features, dropout
        )
        self.transformer_encoder = torch.nn.TransformerEncoder(
            encoder_layers, nlayers
        )

    def forward(self, x):
        return self.transformer_encoder(x.permute(1,0,2)).permute(1,0,2)

# Cell
class SeqAttentionSum(torch.nn.Module):
    def __init__(self, in_features):
        super().__init__()
        self.attn = torch.nn.Sequential(
            torch.nn.Linear(in_features, 1, bias=False),
            torch.nn.Softmax(dim=1),
        )

    def forward(self, x):
        attn = self.attn(x)
        return torch.sum(torch.mul(x, attn), dim=1)


# Cell
class LinearDecoder(torch.nn.Module):
    def __init__(self, in_features, out_features):
        super().__init__()

        self.nn = torch.nn.Sequential(
            torch.nn.Linear(in_features, 64),
            torch.nn.PReLU(),
            torch.nn.Linear(64, out_features),
        )

    def forward(self, x):
        return self.nn(x)

# Cell
class SeqEncoder(torch.nn.Module):
    def __init__(self, in_features, out_features, dropout=0.2, rnn_layer=2):
        super().__init__()

        self.dropout = torch.nn.Dropout(dropout)
        self.input_cnn = SeqCNN(in_features)
        self.hidden_nn = SeqLSTM(in_features*4, out_features, rnn_layer=rnn_layer) #4 for MultiScaleCNN output
        self.attn_sum = SeqAttentionSum(out_features)

    def forward(self, x):
        x = self.input_cnn(x)
        x = self.hidden_nn(x)
        x = self.dropout(x)
        x = self.attn_sum(x)
        return x

# Cell
class SeqDecoder(torch.nn.Module):
    def __init__(self, in_features, out_features):
        super().__init__()

        hidden = 128
        self.rnn_h0 = init_state(1, 1, hidden)
        self.rnn = torch.nn.GRU(
            input_size = in_features,
            hidden_size = hidden,
            num_layers = 1,
            batch_first = True,
            bidirectional = False,
        )

        self.output_nn = torch.nn.Linear(
            hidden, out_features, bias=False
        )

    def forward(self, x):
        h0 = self.rnn_h0.repeat(1, x.size(0), 1)
        x, h = self.rnn(x, h0)
        x = self.output_nn(x)
        return x