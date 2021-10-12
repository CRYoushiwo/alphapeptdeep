# AUTOGENERATED! DO NOT EDIT! File to edit: nbdev_nbs/speclib/predict_lib.ipynb (unless otherwise specified).

__all__ = ['PredictLib']

# Cell
from alphabase.library.library_base import SpecLibBase
from alphadeep.model.msms import pDeepModel
from alphadeep.model.RT import AlphaRTModel
from alphadeep.model.CCS import AlphaCCSModel

class PredictLib(SpecLibBase):
    def __init__(self,
        charged_ion_types, #['b_1+','b_2+','y_1+','y_2+', ...]
        msms_model: pDeepModel,
        rt_model: AlphaRTModel,
        ccs_model: AlphaCCSModel,
        min_frag_mz = 200, max_frag_mz = 2000,
        min_precursor_mz = 500, max_precursor_mz = 2000,
    ):
        super().__init__(
            charged_ion_types,
            min_frag_mz=min_frag_mz,
            max_frag_mz=max_frag_mz,
            min_precursor_mz=min_precursor_mz,
            max_precursor_mz=max_precursor_mz
        )
        self.msms_model = msms_model
        self.rt_model = rt_model
        self.ccs_model = ccs_model

        self.inten_factor = 10000
        self.verbose = True

    @property
    def precursor_df(self):
        return self._precursor_df

    @precursor_df.setter
    def precursor_df(self, df):
        self._precursor_df = df
        self._init_precursor_df()

    def _init_precursor_df(self):
        self._precursor_df['nAA'] = self._precursor_df['sequence'].str.len()
        self._precursor_df['mod_sites'] = self._precursor_df['mod_sites'].astype('U')
        self._precursor_df['charge'] = self._precursor_df['charge'].astype(int)
        # add 'predict_CCS' into columns
        self._precursor_df = self.ccs_model.predict(self._precursor_df, verbose=self.verbose)
        # add 'predict_RT' into columns
        self._precursor_df = self.rt_model.predict(self._precursor_df, verbose=self.verbose)

    def load_fragment_inten_df(self, **kargs):
        if self._fragment_mass_df is None:
            self.load_fragment_mass_df()

        frag_inten_df = self.msms_model.predict(
            self._precursor_df,
            reference_frag_df=self._fragment_mass_df,
            verbose=self.verbose,
        )

        # it does not make sense to
        charged_frag_list = []
        for frag_type in self._fragment_mass_df.columns.values:
            if frag_type in frag_inten_df:
                charged_frag_list.append(frag_type)
        self._fragment_mass_df = self._fragment_mass_df[charged_frag_list]
        self._fragment_inten_df = frag_inten_df[charged_frag_list]*self.inten_factor


