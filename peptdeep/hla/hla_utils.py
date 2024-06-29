import pandas as pd
import numpy as np
import numba

import os

from typing import Union, List

from alphabase.protein.lcp_digest import get_substring_indices

from alphabase.protein.fasta import load_all_proteins

def load_prot_df(
    protein_data:Union[str,list,dict],
)->pd.DataFrame:
    """
    Load protein dataframe from input protein_data.

    Parameters
    ----------
    protein_data : Union[str,list,dict]
        str: fasta file
        list (tuple, or set): a list of fasta files
        dict: protein dict

    Returns
    -------
    pd.DataFrame
        protein dataframe
    """
    if isinstance(protein_data, str):
        protein_dict = load_all_proteins([protein_data])
    elif isinstance(protein_data, (list,tuple,set)):
        protein_dict = load_all_proteins(protein_data)
    elif isinstance(protein_data, str):
        protein_dict = load_all_proteins([protein_data])
    elif isinstance(protein_data, dict):
        protein_dict = protein_data
    else:
        return pd.DataFrame()
    prot_df = pd.DataFrame().from_dict(protein_dict, orient='index')
    prot_df['nAA'] = prot_df.sequence.str.len()
    return prot_df

def cat_proteins(sequences:List[str], sep:str='$')->str:
    """
    Concatenate protein sequences in `prot_df` into a single sequence.

    Parameters
    ----------
    sequences : list
        List-like sequence list.
    sep : str, optional
        Separater of the concat string, by default '$'

    Returns
    -------
    str
        The concat protein sequence.

    Example
    -------
    >>> sequences = ["ABC","DEF"]
    >>> cat_proteins(sequences, sep="$")
    '$ABC$DEF$'
    """
    return sep + sep.join(sequences) + sep

def nonspecific_digest_cat_proteins(
    cat_sequence:str, min_len:int, max_len:int
)->pd.DataFrame:
    """
    Digest the concat protein sequence to non-specific peptides.

    Parameters
    ----------
    cat_sequence : str
        The concat protein sequence generated by :func:`cat_proteins`
    min_len : int
        Min peptide length
    max_len : int
        Max peptide length

    Returns
    -------
    pd.DataFrame
        A dataframe sorted by `nAA` with three columns:
        `start_pos`: the start index of the peptide in cat_protein
        `end_pos`: the stop/end index of the peptide in cat_protein
        `nAA`: the number of amino acids (peptide length).
    """
    pos_starts, pos_ends = get_substring_indices(cat_sequence, min_len, max_len)
    digest_df = pd.DataFrame(dict(start_pos=pos_starts, end_pos=pos_ends))
    digest_df["nAA"] = digest_df.end_pos-digest_df.start_pos
    digest_df.sort_values('nAA', inplace=True)
    digest_df.reset_index(inplace=True, drop=True)
    return digest_df

def _get_rnd_subseq(x, pep_len):
    sequence, prot_len = x
    if prot_len <= pep_len:
        return ''.join(
            [sequence]*(pep_len//prot_len)
        ) + sequence[:pep_len%prot_len]
    start = np.random.randint(0,prot_len-pep_len)
    return sequence[start:start+pep_len]

def get_random_sequences(prot_df:pd.DataFrame, n:int, pep_len:int):
    """
    Random peptide sampling from proteins
    """
    return prot_df.sample(
        n, replace=True, weights='nAA'
    )[['sequence','nAA']].apply(
        _get_rnd_subseq, pep_len=pep_len, axis=1
    ).values.astype('U')

@numba.njit
def check_sty(seq):
    for aa in seq:
        if aa in "STY":
            return True
    return False

def get_seq(x, cat_prot):
    return cat_prot[slice(*x)]

def get_seq_series(df, cat_prot):
    return df[["start_pos","end_pos"]].apply(
        get_seq, axis=1, cat_prot=cat_prot
    )

def check_is_file(file_path:str):
    if os.path.isfile(file_path):
        print(f"Loading `{file_path}`")
        return True
    else:
        print(f"`{file_path}` does not exist, ignore it.")
        return False
