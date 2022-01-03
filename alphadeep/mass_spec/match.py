# AUTOGENERATED! DO NOT EDIT! File to edit: nbdev_nbs/mass_spec/match.ipynb (unless otherwise specified).

__all__ = ['match_centroid_mz', 'PepSpecMatch']

# Cell

import numpy as np
import numba

@numba.njit
def match_centroid_mz(
    spec_mzs:np.array,
    query_mzs:np.array,
    mz_tols:np.array
)->np.array:
    """
    Matched query masses against sorted MS2/spec centroid masses.
    Args:
        spec_mzs (np.array): MS2 or spec mz values, 1-D float array
        query_mzs (np.array): query mz values, n-D float array
        mz_tols (np.array): Da tolerance array, same shape as spec_mzs

    Returns:
        np.array: np.array of int32, the shape is the same as query_mzs.
          -1 means no peaks are matched for the query mz
    """
    idxes = np.searchsorted(spec_mzs, query_mzs)
    ret_indices = np.empty_like(query_mzs, dtype=np.int32)
    # ret_indices[:] = -1
    for i,idx in np.ndenumerate(idxes):
        min_merr = abs(spec_mzs[idx-1]-query_mzs[i])
        min_idx = -1
        if min_merr <= mz_tols[idx-1]:
            min_idx = idx-1
        if idx < len(spec_mzs):
            merr = abs(spec_mzs[idx]-query_mzs[i])
            if merr <= mz_tols[idx] and merr < min_merr:
                min_idx = idx
        ret_indices[i] = min_idx
    return ret_indices


# Cell
import pandas as pd
import numpy as np
from alphadeep.mass_spec.ms_reader import (
    ms2_reader_provider, MSReaderBase
)
from alphabase.peptide.fragment import (
    create_fragment_mz_dataframe,
    get_charged_frag_types
)

class PepSpecMatch(object):
    def __init__(self,
        psm_df: pd.DataFrame,
        fragment_mz_df:pd.DataFrame = None,
        charged_frag_types = get_charged_frag_types(
            ['b','y','b_modloss','y_modloss'],
            2
        ),
    ):
        self.psm_df:pd.DataFrame = psm_df
        if fragment_mz_df is not None:
            self.fragment_mz_df = fragment_mz_df[charged_frag_types]
        else:
            if 'frag_start_idx' in self.psm_df.columns:
                del self.psm_df['frag_start_idx']
                del self.psm_df['frag_end_idx']
            self.fragment_mz_df = create_fragment_mz_dataframe(
                self.psm_df, charged_frag_types
            )
        self._ms2_file_dict = {}

        self.matched_intensity_df = pd.DataFrame(
            np.zeros_like(
                self.fragment_mz_df.values, dtype=np.float64
            ),
            columns=self.fragment_mz_df.columns
        )

        self.matched_mz_err_df = pd.DataFrame(
            np.full_like(
                self.fragment_mz_df.values, np.inf, dtype=np.float64
            ),
            columns=self.fragment_mz_df.columns
        )

    def match_ms2_centroid(self,
        ms2_file_dict: dict, #raw_name: ms2_file_path or ms_reader object
        ms2_file_type:str = 'alphapept', # or 'mgf', or 'thermo'
        ppm=True, tol=20,
    ):
        _grouped = self.psm_df.groupby('raw_name')
        for raw_name, df_group in _grouped:
            if raw_name in ms2_file_dict:
                # pfind does not report RT in the result file
                if isinstance(ms2_file_dict[raw_name], MSReaderBase):
                    ms2_reader = ms2_file_dict[raw_name]
                else:
                    ms2_reader = ms2_reader_provider.get_reader(ms2_file_type)
                    ms2_reader.load(ms2_file_dict[raw_name])
                if 'rt_norm' not in df_group.columns:
                    _df = df_group.merge(
                        ms2_reader.spectrum_df[['spec_idx','rt']],
                        how='left',
                        on='spec_idx',
                    )
                    _df['rt_norm'] = _df.rt/_df.rt.max()
                    self.psm_df.loc[
                        _df.index, ['rt','rt_norm']
                    ] = _df[['rt','rt_norm']]

                for (
                    spec_idx, frag_start_idx, frag_end_idx
                ) in df_group[[
                    'spec_idx', 'frag_start_idx',
                    'frag_end_idx'
                ]].values:
                    (
                        spec_mzs, spec_intens
                    ) = ms2_reader.get_peaks(spec_idx)
                    if len(spec_mzs)==0: continue

                    if ppm:
                        mz_tols = spec_mzs*tol*1e-6
                    else:
                        mz_tols = np.full_like(spec_mzs, tol)

                    frag_mzs = self.fragment_mz_df.values[
                        frag_start_idx:frag_end_idx,:
                    ]

                    matched_idxs = match_centroid_mz(
                        spec_mzs, frag_mzs, mz_tols
                    )
                    matched_intens = spec_intens[matched_idxs]
                    matched_intens[matched_idxs==-1] = 0

                    matched_mass_errs = np.abs(
                        spec_mzs[matched_idxs]-frag_mzs
                    )
                    matched_mass_errs[matched_idxs==-1] = np.inf

                    self.matched_intensity_df.values[
                        frag_start_idx:frag_end_idx,:
                    ] = matched_intens

                    self.matched_mz_err_df.values[
                        frag_start_idx:frag_end_idx,:
                    ] = matched_mass_errs
