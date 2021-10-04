# AUTOGENERATED! DO NOT EDIT! File to edit: nbdev_nbs/model/featurize.ipynb (unless otherwise specified).

__all__ = ['mod_elements', 'mod_feature_size', 'mod_elem_to_idx', 'MOD_TO_FEATURE', 'parse_mod_feature',
           'get_batch_mod_feature', 'parse_aa_indices', 'instrument_dict', 'unknown_inst_index',
           'parse_instrument_indices']

# Cell
import numpy as np
import pandas as pd
from typing import List, Union

from .._settings import const_settings
from alphabase.constants.modification import MOD_CHEM

# Cell
mod_elements = const_settings['mod_elements']
mod_feature_size = len(mod_elements)

mod_elem_to_idx = dict(zip(mod_elements, range(mod_feature_size)))

def _parse_mod_formula(formula):
    '''
    Parse a modification formula to a feature vector
    '''
    feature = np.zeros(mod_feature_size)
    elems = formula.strip(')').split(')')
    for elem in elems:
        chem, num = elem.split('(')
        num  = int(num)
        if chem in mod_elem_to_idx:
            feature[mod_elem_to_idx[chem]] = num
        else:
            feature[-1] += num
    return feature

MOD_TO_FEATURE = {}
for modname, formula in MOD_CHEM.items():
    MOD_TO_FEATURE[modname] = _parse_mod_formula(formula)


# Cell
def parse_mod_feature(
    nAA:int,
    mod_names:List[str],
    mod_sites:List[int]
)->np.array:
    '''
    Get modification feature of a given peptide (len=nAA).
    Note that `site=0` is for peptide N-term modification,
    `site=1` is for peptide C-term modification, and
    `1<=site<=nAA` is for residue modifications on the peptide.
    Args:
        nAA (int): the lenght of the peptide sequence
        mod_names (List[str]): the modification names
        mod_sites (List[str]): the modification sites corresponding
            to `mod_names` on the peptide
    Returns:
        np.array: 2-D feature array with shape `(nAA+2,mod_feature_size)`
    '''
    mod_x = np.zeros((nAA+2,mod_feature_size))
    if mod_names:
        mod_x[mod_sites] = [MOD_TO_FEATURE[mod] for mod in mod_names]
    return mod_x

# Cell
def get_batch_mod_feature(
    df_batch: pd.DataFrame, nAA: int
)->List[np.array]:
    '''
    Args:
        df_batch (pd.DataFrame): dataframe with same-length peptides ('sequence'),
            which contains 'mods' and 'mod_sites' columns
        nAA (int): the length of the same-length peptides
    Returns:
        List[np.array]: a list of 2-D array features
    '''
    mod_x_batch = []
    for mod_names, mod_sites in df_batch[
        ['mods', 'mod_sites']
    ].values:
        if mod_names:
            mod_names = mod_names.split(';')
            mod_sites = [int(site) for site in mod_sites.split(';')]
        else:
            mod_names = []
            mod_sites = []
        mod_x_batch.append(parse_mod_feature(nAA, mod_names, mod_sites))
    return mod_x_batch

# Cell
def parse_aa_indices(
    seq_array: Union[List, np.array]
)->np.array:
    '''
    Convert peptide sequences into AA ID array. ID=0 is reserved for masking,
    so ID of 'A' is 1, ID of 'B' is 2, ..., ID of 'Z' is 27. Zeros is padded
    into the N- and C-term of each sequence after this conversion.
    Args:
        seq_array (Union[List,np.array]):
            list or 1-D array of sequences with the same length
    Returns:
        np.array: 2-D `np.int32` array with the shape
        `(len(seq_array), len(seq_array[0])+2)`. Zeros is padded into the
        N- and C-term of each sequence, so the 1st-D is `len(seq_array[0])+2`.
    '''
    x = np.array(seq_array).view(np.int32).reshape(
            -1, len(seq_array[0])
        )-ord('A')+1
    # padding zeros at the N- and C-term
    return np.pad(x, [(0,0)]*(len(x.shape)-1)+[(1,1)])

# Cell
instrument_dict = dict(
    zip(
        [inst.upper() for inst in const_settings['instruments']],
        range(len(const_settings['instruments']))
    )
)
unknown_inst_index = const_settings['max_instrument_num']-1

# Cell
def parse_instrument_indices(instrument_list):
    instrument_list = [inst.upper() for inst in instrument_list]
    return [
        instrument_dict[inst] if inst in instrument_dict
        else unknown_inst_index for inst in instrument_list
    ]