import os
import random
import warnings
from abc import ABC, abstractmethod
from pathlib import Path
from random import randint
from urllib import request

import astropy.io.ascii
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from aiapy.calibrate import correct_degradation
from aiapy.calibrate.util import get_correction_table
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.visualization import ImageNormalize, LinearStretch, AsinhStretch
from dateutil.parser import parse
from scipy import ndimage
from skimage.measure import block_reduce
from skimage.transform import pyramid_reduce
from sunpy.coordinates import frames
from sunpy.map import Map, all_coordinates_from_map, header_helper


class Editor(ABC):

    def convert(self, data, **kwargs):
        result = self.call(data, **kwargs)
        if isinstance(result, tuple):
            data, add_kwargs = result
            kwargs.update(add_kwargs)
        else:
            data = result
        return data, kwargs

    @abstractmethod
    def call(self, data, **kwargs):
        raise NotImplementedError()

gregor_norms_gband = {'gband': ImageNormalize(vmin=-0.4, vmax=1.4, stretch=LinearStretch(), clip=True)}
gregor_norms_continuum = {'continuum': ImageNormalize(vmin=-0.4, vmax=1.4, stretch=LinearStretch(), clip=True)}



class MapToDataEditor(Editor):
    def call(self, s_map, **kwargs):
        return s_map.data, {"header": s_map.meta}



class NanEditor(Editor):
    def __init__(self, nan=0):
        self.nan = nan

    def call(self, data, **kwargs):
        data = np.nan_to_num(data, nan=self.nan)
        return data



class NormalizeEditor(Editor):
    def __init__(self, norm, **kwargs):
        self.norm = norm

    def call(self, data, **kwargs):
        data = self.norm(data).data * 2 - 1
        return data



class ExpandDimsEditor(Editor):
    def __init__(self, axis=0):
        self.axis = axis

    def call(self, data, **kwargs):
        return np.expand_dims(data, axis=self.axis).astype(np.float32)



class DistributeEditor(Editor):
    def __init__(self, editors):
        self.editors = editors

    def call(self, data, **kwargs):
        return np.concatenate([self.convertData(d, **kwargs) for d in data], 0)

    def convertData(self, data, **kwargs):
        for editor in self.editors:
            data, kwargs = editor.convert(data, **kwargs)
        return data



class LoadGregorGBandEditor(Editor):

    def call(self, file, **kwargs):
        warnings.simplefilter("ignore")
        hdul = fits.open(file)
        #
        assert 'WAVELNTH' in hdul[0].header, 'Invalid GREGOR file %s' % file
        if hdul[0].header['WAVELNTH'] == 430.7:
            index = 0
        elif hdul[1].header['WAVELNTH'] == 430.7:
            index = 1
        else:
            raise Exception('Invalid GREGOR file %s' % file)
        #
        primary_header = hdul[0].header
        primary_header['cunit1'] = 'arcsec'
        primary_header['cunit2'] = 'arcsec'
        primary_header['cdelt1'] = 0.0253 / 2560
        primary_header['cdelt2'] = 0.0253 / 2160
        #
        g_band = hdul[index::2]
        g_band = sorted(g_band, key=lambda hdu: hdu.header['TIMEOFFS'])
        #
        gregor_maps = [Map(hdu.data, primary_header) for hdu in g_band]
        return gregor_maps, {'path': file}



class LoadGregorContinuumEditor(Editor):

    def call(self, file, **kwargs):
        warnings.simplefilter('ignore')
        hdul = fits.open(file)

        assert 'WAVELNTH' in hdul[0].header, 'Invalid GREGOR file %s' % file
        if hdul[0].header['WAVELNTH'] == 450.55:
            index = 0
        elif hdul[1].header['WAVELNTH'] == 450.55:
            index = 1
        else:
            raise Exception('Invalid GREGOR file %s' % file)

        primary_header = hdul[0].header
        primary_header['cunit'] = 'arcsec'
        primary_header['cunit2'] = 'arcsec'
        primary_header['cdelt1'] = 0.0253 / 2560
        primary_header['cdelt2'] = 0.0253 / 2160

        continuum = hdul[index::2]
        continuum = sorted(continuum, key=lambda hdu: hdu.header['TIMEOFFS'])
        gregor_maps = [Map(hdu.data, primary_header) for hdu in continuum]
        return gregor_maps, {'path': file}

    
    
class PaddingEditor(Editor):
    def __init__(self, target_shape):
        self.target_shape = target_shape

    def call(self, data, **kwargs):
        s = data.shape
        p = self.target_shape
        x_pad = (p[0] - s[-2]) / 2
        y_pad = (p[1] - s[-1]) / 2
        pad = [(int(np.floor(x_pad)), int(np.ceil(x_pad))),
               (int(np.floor(y_pad)), int(np.ceil(y_pad)))]
        if len(s) == 3:
            pad.insert(0, (0, 0))
        return np.pad(data, pad, 'constant', constant_values=np.nan)

    
    
class UnpaddingEditor(Editor):
    def __init__(self, target_shape):
        self.target_shape = target_shape

    def call(self, data, **kwargs):
        s = data.shape
        p = self.target_shape
        x_unpad = (s[-2] - p[0]) / 2
        y_unpad = (s[-1] - p[1]) / 2
        #
        unpad = [None if int(np.floor(y_unpad)) == 0 else int(np.floor(y_unpad)),
                 None if int(np.ceil(y_unpad)) == 0 else int(np.ceil(y_unpad)),
                 (None if int(np.floor(x_unpad)) == 0 else int(np.floor(x_unpad)),
                  None if int(np.ceil(x_unpad)) == 0 else int(np.ceil(x_unpad)))]
        data = data[:, unpad[0][0]:unpad[0][1], unpad[1][0]:unpad[1][1]]
        return data
