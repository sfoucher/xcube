import unittest
from typing import Any, Dict, Tuple

import numpy as np
import xarray as xr

from xcube.core.chunk import chunk_dataset
from xcube.core.compute import CubeFuncOutput
from xcube.core.compute import compute_cube
from xcube.core.new import new_cube
from xcube.core.schema import CubeSchema


class ComputeCubeTest(unittest.TestCase):

    def setUp(self) -> None:
        cube = new_cube(width=360,
                        height=180,
                        time_periods=6,
                        variables=dict(analysed_sst=275.3,
                                       analysis_error=2.1))
        cube = chunk_dataset(cube, dict(time=3, lat=90, lon=90))
        self.cube = cube

    def test_without_inputs(self):
        calls = []

        def my_cube_func(input_params: Dict[str, Any] = None,
                         dim_coords: Dict[str, np.ndarray] = None,
                         dim_ranges: Dict[str, Tuple[int, int]] = None) -> CubeFuncOutput:
            nonlocal calls
            calls.append((input_params, dim_coords, dim_ranges))
            lon_range = dim_ranges['lon']
            lat_range = dim_ranges['lat']
            time_range = dim_ranges['time']
            n_lon = lon_range[1] - lon_range[0]
            n_lat = lat_range[1] - lat_range[0]
            n_time = time_range[1] - time_range[0]
            fill_value = input_params['fill_value']
            return np.full((n_time, n_lat, n_lon), fill_value, dtype=np.float64)

        output_cube = compute_cube(my_cube_func,
                                   input_cube_schema=CubeSchema.new(self.cube),
                                   input_params=dict(fill_value=0.74))

        self.assertIsInstance(output_cube, xr.Dataset)
        self.assertIn('output', output_cube.data_vars)
        output_var = output_cube.output
        self.assertEqual(0, len(calls))
        self.assertEqual(('time', 'lat', 'lon'), output_var.dims)
        self.assertEqual((6, 180, 360), output_var.shape)

        values = output_var.values
        self.assertEqual(2 * 2 * 4, len(calls))
        self.assertEqual((6, 180, 360), values.shape)
        self.assertEqual(0.74, values[0, 0, 0])
        self.assertEqual(0.74, values[-1, -1, -1])

    def test_from_two_inputs(self):
        calls = []

        def my_cube_func(analysed_sst: np.ndarray,
                         analysis_error: np.ndarray,
                         input_params: Dict[str, Any] = None,
                         dim_coords: Dict[str, np.ndarray] = None,
                         dim_ranges: Dict[str, Tuple[int, int]] = None) -> CubeFuncOutput:
            nonlocal calls
            calls.append((analysed_sst, analysis_error, input_params, dim_coords, dim_ranges))
            return analysed_sst.data + input_params['factor'] * analysis_error

        output_cube = compute_cube(my_cube_func,
                                   self.cube,
                                   input_var_names=['analysed_sst', 'analysis_error'],
                                   input_params=dict(factor=0.5))

        self.assertIsInstance(output_cube, xr.Dataset)
        self.assertIn('output', output_cube.data_vars)
        output_var = output_cube.output
        self.assertEqual(0, len(calls))
        self.assertEqual(('time', 'lat', 'lon'), output_var.dims)
        self.assertEqual((6, 180, 360), output_var.shape)

        values = output_var.values
        self.assertEqual(2 * 2 * 4, len(calls))
        self.assertEqual((6, 180, 360), values.shape)
        self.assertEqual(275.3 + 0.5 * 2.1, values[0, 0, 0])
        self.assertEqual(275.3 + 0.5 * 2.1, values[-1, -1, -1])

    def test_invalid_cube_func(self):
        def my_cube_func(analysis_error: np.ndarray) -> CubeFuncOutput:
            return analysis_error + 1

        with self.assertRaises(ValueError) as cm:
            compute_cube(my_cube_func,
                         self.cube,
                         input_var_names=['analysed_sst', 'analysis_error'],
                         input_params=dict(factor=0.5))
        self.assertEqual("invalid cube_func 'my_cube_func': expected 2 arguments, "
                         "but got analysis_error",
                         f'{cm.exception}')

        def my_cube_func(analysed_sst, dim_coords, analysis_error) -> CubeFuncOutput:
            return analysed_sst + analysis_error

        with self.assertRaises(ValueError) as cm:
            compute_cube(my_cube_func,
                         self.cube,
                         input_var_names=['analysed_sst', 'analysis_error'],
                         input_params=dict(factor=0.5))
        self.assertEqual("invalid cube_func 'my_cube_func': "
                         "any argument must occur before any of input_params, dim_coords, dim_ranges, "
                         "but got analysed_sst, dim_coords, analysis_error",
                         f'{cm.exception}')

        def my_cube_func(dim_ranges, *vars) -> CubeFuncOutput:
            return vars[0] + vars[0]

        with self.assertRaises(ValueError) as cm:
            compute_cube(my_cube_func,
                         self.cube,
                         input_var_names=['analysed_sst', 'analysis_error'],
                         input_params=dict(factor=0.5))
        self.assertEqual("invalid cube_func 'my_cube_func': "
                         "any argument must occur before any of input_params, dim_coords, dim_ranges, "
                         "but got dim_ranges before *vars",
                         f'{cm.exception}')

    def test_inspect(self):
        def func():
            pass

        self._assert_inspect(func, exp_args=[], exp_kwonlyargs=[], exp_annotations={})

        # noinspection PyUnusedLocal
        def func(*input_vars):
            pass

        self._assert_inspect(func,
                             exp_args=[],
                             exp_varargs='input_vars',
                             exp_kwonlyargs=[],
                             exp_annotations={})

        # noinspection PyUnusedLocal
        def func(*input_vars, input_params, dim_coords):
            pass

        self._assert_inspect(func,
                             exp_args=[],
                             exp_varargs='input_vars',
                             exp_kwonlyargs=['input_params', 'dim_coords'],
                             exp_annotations={})

        # noinspection PyUnusedLocal
        def func(a, b, c, *input_vars, dim_ranges, input_params):
            pass

        self._assert_inspect(func,
                             exp_args=['a', 'b', 'c'],
                             exp_varargs='input_vars',
                             exp_kwonlyargs=['dim_ranges', 'input_params'],
                             exp_annotations={})

        # noinspection PyUnusedLocal
        def func(a, b, c, dim_ranges, input_params):
            pass

        self._assert_inspect(func,
                             exp_args=['a', 'b', 'c', 'dim_ranges', 'input_params'],
                             exp_kwonlyargs=[],
                             exp_annotations={})

    def _assert_inspect(self,
                        func,
                        exp_args=None,
                        exp_varargs=None,
                        exp_varkw=None,
                        exp_defaults=None,
                        exp_kwonlyargs=None,
                        exp_kwonlydefaults=None,
                        exp_annotations=None):
        import inspect
        argspec = inspect.getfullargspec(func)
        self.assertEqual(exp_args, argspec[0], msg='args')
        self.assertEqual(exp_varargs, argspec[1], msg='varargs')
        self.assertEqual(exp_varkw, argspec[2], msg='varkw')
        self.assertEqual(exp_defaults, argspec[3], msg='defaults')
        self.assertEqual(exp_kwonlyargs, argspec[4], msg='kwonlyargs')
        self.assertEqual(exp_kwonlydefaults, argspec[5], msg='kwonlydefaults')
        self.assertEqual(exp_annotations, argspec[6], msg='annotations')
