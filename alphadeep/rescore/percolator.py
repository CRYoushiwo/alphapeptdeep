# AUTOGENERATED! DO NOT EDIT! File to edit: nbdev_nbs/rescore/percolator.ipynb (unless otherwise specified).

__all__ = ['perc_settings', 'Percolator']

# Cell
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
import numpy as np
import pandas as pd
from tqdm import tqdm
import torch

from alphadeep.utils import logging

from alphabase.peptide.fragment import get_charged_frag_types

from alphadeep.rescore.feature_extractor import (
    ScoreFeatureExtractor,
    ScoreFeatureExtractorMP
)

from alphadeep.rescore.fdr import (
    fdr_from_ref, fdr_to_q_values, calc_fdr_for_df
)

from alphadeep.pretrained_models import ModelManager

from alphadeep.settings import global_settings

perc_settings = global_settings['percolator']

# Cell

class Percolator:
    def __init__(self,
        *,
        ml_type=perc_settings['ml_type'],
        cv_fold = perc_settings['cv_fold'],
        n_iteration = perc_settings['n_ml_iter'],
        ms2_ppm = perc_settings['ms2_ppm'],
        ms2_tol = perc_settings['ms2_tol'],
        model_mgr:ModelManager = None,
        **sklearn_kwargs
    ):
        """

        Args:
            ml_type ([type], optional): machine learning type,
              Defaults to perc_settings['ml_type'].
            n_iteration ([type], optional): [description].
              Defaults to perc_settings['n_ml_iter'].
            ms2_ppm ([type], optional): [description].
              Defaults to perc_settings['ms2_ppm'].
            ms2_tol ([type], optional): [description].
              Defaults to perc_settings['ms2_tol'].
            model_mgr (ModelManager, optional): [description].
              Defaults to None.
        """
        if model_mgr is None:
            self.model_mgr = ModelManager()
            self.model_mgr.load_installed_models(
                perc_settings['model_type'],
                mask_modloss=perc_settings[
                    'mask_modloss'
                ]
            )
        else:
            self.model_mgr = model_mgr
        self.charged_frag_types = perc_settings['frag_types']
        self.ms2_ppm = ms2_ppm
        self.ms2_tol = ms2_tol
        if ml_type == 'logistic_regression':
            ml_type = 'lr'
        self.ml_type = ml_type
        self.fdr_level = perc_settings['fdr_level']
        self.fdr = perc_settings['fdr']
        self.cv_fold = cv_fold
        self.n_iter = n_iteration

        if ml_type == 'lr':
            self.model = LogisticRegression(
                solver='liblinear', **sklearn_kwargs
            )
        else:
            self.model = RandomForestClassifier(**sklearn_kwargs)

        if perc_settings['multiprocessing']:
            self.feature_extractor = ScoreFeatureExtractorMP(
                model_mgr=self.model_mgr,
            )
        else:
            self.feature_extractor = ScoreFeatureExtractor(
                model_mgr=self.model_mgr,
            )
        self.feature_list = [
            f for f in self.feature_extractor.score_feature_list
        ]
        self.feature_list += ['score','nAA','charge']
        self.feature_list.append('ml_score') #self-boosted
        psm_type = perc_settings['input_files']['psm_type']
        self.feature_list += list(perc_settings['input_files'][
            'other_score_column_mapping'
        ][psm_type].keys())

        self.max_train_sample = perc_settings['max_perc_train_sample']
        self.min_train_sample = perc_settings['min_perc_train_sample']

    def enable_model_fine_tuning(self, flag=True):
        self.feature_extractor.require_model_tuning = flag
        self.feature_extractor.require_raw_specific_rt_tuning = flag
    def disable_model_fine_tuning(self):
        self.feature_extractor.require_model_tuning = False
        self.feature_extractor.require_raw_specific_rt_tuning = False

    def _estimate_fdr(self, df:pd.DataFrame, fdr_level=None)->pd.DataFrame:
        df = df.sort_values(['ml_score','decoy'], ascending=False)
        df = df.reset_index(drop=True)
        if not fdr_level: fdr_level = self.fdr_level
        if fdr_level == 'psm':
            target_values = 1-df['decoy'].values
            decoy_cumsum = np.cumsum(df['decoy'].values)
            target_cumsum = np.cumsum(target_values)
            fdr_values = decoy_cumsum/target_cumsum
            df['fdr'] = fdr_to_q_values(fdr_values)
        else:
            if fdr_level == 'precursor':
                _df = df.groupby([
                    'sequence','mods','mod_sites','charge','decoy'
                ])['ml_score'].max()
            elif fdr_level == 'peptide':
                _df = df.groupby([
                    'sequence','mods','mod_sites','decoy'
                ])['ml_score'].max()
            else:
                _df = df.groupby(['sequence','decoy'])['ml_score'].max()
            _df = _df.reset_index(drop=True)
            _df = _df.sort_values(['ml_score','decoy'], ascending=False)
            target_values = 1-_df['decoy'].values
            decoy_cumsum = np.cumsum(_df['decoy'].values)
            target_cumsum = np.cumsum(target_values)
            fdr_values = decoy_cumsum/target_cumsum
            _df['fdr'] = fdr_to_q_values(fdr_values)
            df['fdr'] = fdr_from_ref(
                df['ml_score'].values, _df['ml_score'].values,
                _df['fdr'].values
            )
        return df

    def _train(self, train_t_df, train_d_df):
        if len(train_t_df) > self.max_train_sample:
            train_t_df = train_t_df.sample(
                n=self.max_train_sample,
                random_state=1337
            )
        if len(train_d_df) > self.max_train_sample:
            train_d_df = train_d_df.sample(
                n=self.max_train_sample,
                random_state=1337
            )

        train_df = pd.concat((train_t_df, train_d_df))
        train_label = np.ones(len(train_df),dtype=np.int32)
        train_label[len(train_t_df):] = 0

        self.model.fit(
            train_df[self.feature_list].values,
            train_label
        )

    def _predict(self, test_df):
        if self.ml_type == 'lr':
            test_df['ml_score'] = self.model.decision_function(
                test_df[self.feature_list].values
            )
        else:
            test_df['ml_score'] = self.model.predict_proba(
                test_df[self.feature_list].values
            )[:,1]
        return test_df

    def _cv_score(self, df:pd.DataFrame)->pd.DataFrame:
        df = df.sample(
            frac=1, random_state=1337
        ).reset_index(drop=True)
        df_target = df[df.decoy == 0]
        df_decoy = df[df.decoy != 0]
        if (
            np.sum(df_target.fdr<0.01) <
            self.min_train_sample*self.cv_fold
            or len(df_decoy) < self.min_train_sample*self.cv_fold
        ):
            logging.info(
                f'#target={np.sum(df_target.fdr<0.01)} or #decoy={len(df_decoy)} '
                f'less then minimum training sample {self.min_train_sample} '
                f'for cv-fold={self.cv_fold}'
            )
            return df

        if self.cv_fold > 1:
            test_df_list = []
            for i in range(self.cv_fold):
                t_mask = np.ones(len(df_target), dtype=bool)
                _slice = slice(i, len(df_target), self.cv_fold)
                t_mask[_slice] = False
                cv_df_target = df_target[t_mask]
                train_t_df = cv_df_target[
                    cv_df_target.fdr <= self.fdr
                ]
                test_t_df = df_target[_slice]

                d_mask = np.ones(len(df_decoy), dtype=bool)
                _slice = slice(i, len(df_decoy), self.cv_fold)
                d_mask[_slice] = False
                train_d_df = df_decoy[d_mask]
                test_d_df = df_decoy[_slice]

                self._train(train_t_df, train_d_df)

                test_df = pd.concat((test_t_df, test_d_df))
                test_df_list.append(self._predict(test_df))

            return pd.concat(test_df_list)
        else:
            train_t_df = df_target[df_target.fdr <= self.fdr]

            self._train(train_t_df, df_decoy)
            test_df = pd.concat((df_target, df_decoy))

            return self._predict(test_df)

    def extract_features(self,
        psm_df:pd.DataFrame, ms2_file_dict:dict, ms2_file_type:str
    )->pd.DataFrame:
        for feat in self.feature_list:
            if feat not in psm_df.columns:
                self.feature_list.remove(feat)

        psm_df['ml_score'] = psm_df.score
        psm_df = self._estimate_fdr(psm_df, 'psm')
        psm_df = self.feature_extractor.extract_features(
            psm_df, ms2_file_dict,
            ms2_file_type,
            frag_types=self.charged_frag_types,
            ms2_ppm=self.ms2_ppm, ms2_tol=self.ms2_tol
        )
        return psm_df

    def re_score(self, df:pd.DataFrame)->pd.DataFrame:
        logging.info(
            f'{np.sum((df.fdr<=self.fdr) & (df.decoy==0))} '
            f'target PSMs at {self.fdr} psm-level FDR'
        )
        for i in range(self.n_iter):
            logging.info(f'[PERC] Iteration {i+1} of Percolator ...')
            df = self._cv_score(df)
            df = self._estimate_fdr(df, 'psm')
            logging.info(
                f'[PERC] {len(df[(df.fdr<=self.fdr) & (df.decoy==0)])} '
                f'target PSMs at {self.fdr} psm-level FDR'
            )
        df = self._estimate_fdr(df)
        logging.info(
            f'{len(df[(df.fdr<=self.fdr) & (df.decoy==0)])} '
            f'target PSMs at {self.fdr} {self.fdr_level}-level FDR'
        )
        return df

    def run(self,
        psm_df:pd.DataFrame, ms2_file_dict:dict, ms2_file_type:str
    )->pd.DataFrame:
        df = self.extract_features(
            psm_df, ms2_file_dict, ms2_file_type
        )
        return self.re_score(df)