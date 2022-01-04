# AUTOGENERATED! DO NOT EDIT! File to edit: nbdev_nbs/mass_spec/match.ipynb (unless otherwise specified).

__all__ = ['match_centroid_mz', 'numba_match_one_raw', 'THREAD_NUM', 'PepSpecMatch']

# Cell

import numpy as np
import numba

@numba.njit(nogil=True)
def match_centroid_mz(
    spec_mzs:np.array,
    query_mzs:np.array,
    spec_mz_tols:np.array
)->np.array:
    """
    Matched query masses against sorted MS2/spec centroid masses.
    Args:
        spec_mzs (np.array): MS2 or spec mz values, 1-D float array
        query_mzs (np.array): query mz values, n-D float array
        spec_mz_tols (np.array): Da tolerance array, same shape as spec_mzs

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
        if min_merr <= spec_mz_tols[idx-1]:
            min_idx = idx-1
        if idx < len(spec_mzs):
            merr = abs(spec_mzs[idx]-query_mzs[i])
            if merr <= spec_mz_tols[idx] and merr < min_merr:
                min_idx = idx
        ret_indices[i] = min_idx
    return ret_indices


# Cell
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor as Executor

from alphadeep.mass_spec.ms_reader import (
    ms2_reader_provider, MSReaderBase
)
from alphabase.peptide.fragment import (
    create_fragment_mz_dataframe,
    get_charged_frag_types
)

from alphadeep._settings import global_settings
THREAD_NUM = global_settings['thread_num']

@numba.njit(nogil=True, cache=True)
def numba_match_one_raw(
    spec_idxes, frag_start_idxes, frag_end_idxes,
    all_frag_mzs,
    all_spec_mzs, all_spec_intensities,
    peak_start_idxes, peak_end_idxes,
    matched_intensities, matched_mz_errs,
    ppm, tol,
):
    for spec_idx, frag_start, frag_end in zip(
        spec_idxes, frag_start_idxes, frag_end_idxes
    ):
        peak_start = peak_start_idxes[spec_idx]
        peak_end = peak_end_idxes[spec_idx]
        if peak_end == peak_start: continue
        spec_mzs = all_spec_mzs[peak_start:peak_end]
        spec_intens = all_spec_intensities[peak_start:peak_end]

        if ppm:
            spec_mz_tols = spec_mzs*tol*1e-6
        else:
            spec_mz_tols = np.full_like(spec_mzs, tol)

        frag_mzs = all_frag_mzs[frag_start:frag_end,:].copy()

        matched_idxes = match_centroid_mz(
            spec_mzs, frag_mzs, spec_mz_tols
        ).reshape(-1)

        matched_intens = spec_intens[matched_idxes]
        matched_intens[matched_idxes==-1] = 0

        matched_mass_errs = np.abs(
            spec_mzs[
                matched_idxes.reshape(-1)
            ]-frag_mzs.reshape(-1)
        )
        matched_mass_errs[matched_idxes==-1] = np.inf

        matched_intensities[
            frag_start:frag_end,:
        ] = matched_intens.reshape(frag_mzs.shape)

        matched_mz_errs[
            frag_start:frag_end,:
        ] = matched_mass_errs.reshape(frag_mzs.shape)


# Cell
class PepSpecMatch(object):
    def __init__(self,
        psm_df: pd.DataFrame,
        charged_frag_types = get_charged_frag_types(
            ['b','y','b_modloss','y_modloss'],
            2
        ),
    ):
        self.mp_exec = Executor(THREAD_NUM)

        self.psm_df = psm_df

        if 'frag_start_idx' in self.psm_df.columns:
            del self.psm_df['frag_start_idx']
            del self.psm_df['frag_end_idx']

        self.fragment_mz_df = create_fragment_mz_dataframe(
            self.psm_df, charged_frag_types
        )

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

    def _match_ms2_centroid_one_raw_numba(self, raw_name, df_group):
        if raw_name in self._ms2_file_dict:
            # pfind does not report RT in the result file
            if isinstance(self._ms2_file_dict[raw_name], MSReaderBase):
                ms2_reader = self._ms2_file_dict[raw_name]
            else:
                ms2_reader = ms2_reader_provider.get_reader(
                    self._ms2_file_type
                )
                ms2_reader.load(self._ms2_file_dict[raw_name])
            if self.rt_not_in_df:
                _df = df_group.reset_index().merge(
                    ms2_reader.spectrum_df[['spec_idx','rt']],
                    how='left',
                    on='spec_idx',
                ).set_index('index')

                _df['rt_norm'] = _df.rt/_df.rt.max()
                self.psm_df.loc[
                    _df.index, ['rt','rt_norm']
                ] = _df[['rt','rt_norm']]

            numba_match_one_raw(
                df_group.spec_idx.values,
                df_group.frag_start_idx.values,
                df_group.frag_end_idx.values,
                self.fragment_mz_df.values,
                ms2_reader.mzs, ms2_reader.intensities,
                ms2_reader.spectrum_df.peak_start_idx.values,
                ms2_reader.spectrum_df.peak_end_idx.values,
                self.matched_intensity_df.values,
                self.matched_mz_err_df.values,
                self.ppm, self.tol
            )

    def _match_ms2_centroid_one_raw(self, raw_name, df_group):
        if raw_name in self._ms2_file_dict:
            # pfind does not report RT in the result file
            if isinstance(self._ms2_file_dict[raw_name], MSReaderBase):
                ms2_reader = self._ms2_file_dict[raw_name]
            else:
                ms2_reader = ms2_reader_provider.get_reader(
                    self._ms2_file_type
                )
                ms2_reader.load(self._ms2_file_dict[raw_name])
            if self.rt_not_in_df:
                _df = df_group.reset_index().merge(
                    ms2_reader.spectrum_df[['spec_idx','rt']],
                    how='left',
                    on='spec_idx',
                ).set_index('index')

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

                if self.ppm:
                    mz_tols = spec_mzs*self.tol*1e-6
                else:
                    mz_tols = np.full_like(spec_mzs, self.tol)

                frag_mzs = self.fragment_mz_df.values[
                    frag_start_idx:frag_end_idx,:
                ]

                matched_idxes = match_centroid_mz(
                    spec_mzs, frag_mzs, mz_tols
                )
                matched_intens = spec_intens[matched_idxes]
                matched_intens[matched_idxes==-1] = 0

                matched_mass_errs = np.abs(
                    spec_mzs[matched_idxes]-frag_mzs
                )
                matched_mass_errs[matched_idxes==-1] = np.inf

                self.matched_intensity_df.values[
                    frag_start_idx:frag_end_idx,:
                ] = matched_intens

                self.matched_mz_err_df.values[
                    frag_start_idx:frag_end_idx,:
                ] = matched_mass_errs

    def match_ms2_centroid(self,
        ms2_file_dict: dict, #raw_name: ms2_file_path or ms_reader object
        ms2_file_type:str = 'alphapept', # or 'mgf', or 'thermo'
        ppm=True, tol=20.0,
    ):
        self._ms2_file_dict = ms2_file_dict
        self._ms2_file_type = ms2_file_type
        self.ppm = ppm
        self.tol = tol

        if 'rt_norm' not in self.psm_df.columns:
            self.rt_not_in_df = True
        else:
            self.rt_not_in_df = False
        for raw_name, df_group in self.psm_df.groupby('raw_name'):
            self._match_ms2_centroid_one_raw_numba(raw_name, df_group)
            # self.mp_exec.submit(
            #     self._match_ms2_centroid_one_raw_numba, raw_name, df_group
            # )