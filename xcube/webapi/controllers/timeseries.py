# The MIT License (MIT)
# Copyright (c) 2020 by the xcube development team and contributors
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of
# this software and associated documentation files (the "Software"), to deal in
# the Software without restriction, including without limitation the rights to
# use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
# of the Software, and to permit persons to whom the Software is furnished to do
# so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""
This module implements the controller for the "/ts" handler.
It is maintained for legacy reasons only (DCS4COP VITO viewer).
"""

from typing import Dict, List, Optional, Union, Sequence, Any, Set, Tuple

import numpy as np
import shapely.geometry
import xarray as xr

from xcube.constants import LOG
from xcube.core import timeseries
from xcube.core.ancvar import find_ancillary_var_names
from xcube.core.timecoord import timestamp_to_iso_string
from xcube.util.geojson import GeoJSON
from xcube.util.perf import measure_time
from xcube.webapi.context import ServiceContext
from xcube.webapi.errors import ServiceBadRequestError

TimeSeriesValue = Dict[str, Union[str, bool, int, float, None]]
TimeSeries = List[TimeSeriesValue]
TimeSeriesCollection = List[TimeSeries]
GeoJsonObj = Dict[str, Any]
GeoJsonFeature = GeoJsonObj
GeoJsonGeometry = GeoJsonObj


def get_time_series(ctx: ServiceContext,
                    ds_name: str, var_name: str,
                    geo_json: GeoJsonObj,
                    agg_methods: Union[str, Sequence[str]] = None,
                    start_date: np.datetime64 = None,
                    end_date: np.datetime64 = None,
                    max_valids: int = None,
                    incl_ancillary_vars: bool = False) -> Union[TimeSeries, TimeSeriesCollection]:
    """
    Get the time-series for a given GeoJSON object *geo_json*.

    If *geo_json* is a single geometry or feature a list of time-series values is returned.
    If *geo_json* is a single geometry collection or feature collection a collection of lists of time-series values
    is returned so that geometry/feature collection and time-series collection elements are corresponding
    at same indices.

    A time series value always contains the key "time" whose value is an ISO date/time string. Other entries
    are values with varying keys depending on the geometry type and *agg_methods*. Their values may be
    either a bool, int, float or None.
    For point geometries the second key is "value".
    For non-point geometries that cover spatial areas, there will be values for all keys given by *agg_methods*.

    :param ctx: Service context object
    :param ds_name: The dataset identifier.
    :param var_name: The variable name.
    :param geo_json: The GeoJSON object that is a or has a geometry or collection of geometryies.
    :param start_date: An optional start date.
    :param end_date: An optional end date.
    :param agg_methods: Spatial aggregation methods for geometries that cover a spatial area.
    :param max_valids: Optional number of valid points.
           If it is None (default), also missing values are returned as NaN;
           if it is -1 only valid values are returned;
           if it is a positive integer, the most recent valid values are returned.
    :param incl_ancillary_vars: For point geometries, include values of ancillary variables, if any.
    :return: Time-series data structure.
    """
    agg_methods = timeseries.normalize_agg_methods(agg_methods, exception_type=ServiceBadRequestError)

    dataset = ctx.get_time_series_dataset(ds_name, var_name=var_name)
    geo_json_geometries, is_collection = _to_geo_json_geometries(geo_json)
    geometries = _to_shapely_geometries(geo_json_geometries)

    with measure_time() as time_result:
        results = _get_time_series_for_geometries(dataset,
                                                  var_name,
                                                  geometries,
                                                  start_date=start_date,
                                                  end_date=end_date,
                                                  agg_methods=agg_methods,
                                                  max_valids=max_valids,
                                                  incl_ancillary_vars=incl_ancillary_vars)

    if ctx.trace_perf:
        LOG.info(f'get_time_series: dataset id {ds_name}, variable {var_name}, '
                 f'{len(results)} x {len(results[0])} values, took {time_result.duration} seconds')

    return results[0] if not is_collection and len(results) == 1 else results


def _get_time_series_for_geometries(dataset: xr.Dataset,
                                    var_name: str,
                                    geometries: List[shapely.geometry.base.BaseGeometry],
                                    agg_methods: Set[str],
                                    start_date: np.datetime64 = None,
                                    end_date: np.datetime64 = None,
                                    max_valids=None,
                                    incl_ancillary_vars=False) -> TimeSeriesCollection:
    time_series_collection = []
    for geometry in geometries:
        time_series = _get_time_series_for_geometry(dataset,
                                                    var_name,
                                                    geometry,
                                                    agg_methods,
                                                    start_date=start_date,
                                                    end_date=end_date,
                                                    max_valids=max_valids,
                                                    incl_ancillary_vars=incl_ancillary_vars)
        time_series_collection.append(time_series)
    return time_series_collection


def _get_time_series_for_geometry(dataset: xr.Dataset,
                                  var_name: str,
                                  geometry: shapely.geometry.base.BaseGeometry,
                                  agg_methods: Set[str],
                                  start_date: np.datetime64 = None,
                                  end_date: np.datetime64 = None,
                                  max_valids: int = None,
                                  incl_ancillary_vars=False) -> TimeSeries:
    if isinstance(geometry, shapely.geometry.Point):
        return _get_time_series_for_point(dataset, var_name,
                                          geometry,
                                          agg_methods,
                                          start_date=start_date,
                                          end_date=end_date,
                                          max_valids=max_valids,
                                          incl_ancillary_vars=incl_ancillary_vars)

    time_series_ds = timeseries.get_time_series(dataset,
                                                geometry,
                                                [var_name],
                                                agg_methods=agg_methods,
                                                start_date=start_date,
                                                end_date=end_date)
    if time_series_ds is None:
        return []

    var_names = {agg_method: f'{var_name}_{agg_method}' for agg_method in agg_methods}

    return _collect_timeseries_result(time_series_ds,
                                      var_names,
                                      max_valids=max_valids)


def _get_time_series_for_point(dataset: xr.Dataset,
                               var_name: str,
                               point: shapely.geometry.Point,
                               agg_methods: Set[str],
                               start_date: np.datetime64 = None,
                               end_date: np.datetime64 = None,
                               max_valids: int = None,
                               incl_ancillary_vars=False) -> TimeSeries:
    var_key = None
    if timeseries.AGG_MEAN in agg_methods:
        var_key = timeseries.AGG_MEAN
    elif timeseries.AGG_MEDIAN in agg_methods:
        var_key = timeseries.AGG_MEDIAN
    elif timeseries.AGG_MIN in agg_methods or timeseries.AGG_MAX in agg_methods:
        var_key = timeseries.AGG_MIN
    if not var_key:
        raise RuntimeError('Aggregation methods must include one of "mean", "median", "min", "max"')

    roles_to_anc_var_names = dict()
    if incl_ancillary_vars:
        roles_to_anc_var_name_sets = find_ancillary_var_names(dataset, var_name, same_shape=True, same_dims=True)
        for role, roles_to_anc_var_name_sets in roles_to_anc_var_name_sets.items():
            if role:
                roles_to_anc_var_names[role] = roles_to_anc_var_name_sets.pop()

    var_names = [var_name] + list(set(roles_to_anc_var_names.values()))

    time_series_ds = timeseries.get_time_series(dataset, point, var_names, start_date=start_date, end_date=end_date)
    if time_series_ds is None:
        return []

    key_to_var_names = {var_key: var_name}
    for role, anc_var_name in roles_to_anc_var_names.items():
        key_to_var_names[role] = anc_var_name

    return _collect_timeseries_result(time_series_ds,
                                      key_to_var_names,
                                      max_valids=max_valids)


def _collect_timeseries_result(time_series_ds: xr.Dataset,
                               key_to_var_names: Dict[str, str],
                               max_valids: int = None) -> TimeSeries:
    if not (max_valids is None or max_valids == -1 or max_valids > 0):
        raise ValueError('max_valids must be either None, -1 or positive')

    vars = {key: time_series_ds[var_name] for key, var_name in key_to_var_names.items()}
    time = time_series_ds.time
    max_number_of_observations = time_series_ds.attrs.get('max_number_of_observations', 1)
    num_times = time.size
    time_series = []

    max_valids_is_pos = max_valids is not None and max_valids > 0
    if max_valids_is_pos:
        time_indexes = range(num_times - 1, -1, -1)
    else:
        time_indexes = range(num_times)

    for time_index in time_indexes:

        if len(time_series) == max_valids:
            break

        time_series_value = dict()
        all_null = True
        for key, var in vars.items():
            var_values = var.values
            var_value = var_values[time_index]
            if np.isfinite(var_value):
                all_null = False
                if np.issubdtype(var_value.dtype, np.floating):
                    var_value = float(var_value)
                elif np.issubdtype(var_value.dtype, np.integer):
                    var_value = int(var_value)
                elif np.issubdtype(var_value.dtype, np.dtype(bool)):
                    var_value = bool(var_value)
                else:
                    raise ValueError(f'cannot convert {var_value.dtype} into JSON-convertible value')
            else:
                var_value = None
            time_series_value[key] = var_value

        has_count = 'count' in time_series_value
        no_obs = all_null or (has_count and time_series_value['count'] == 0)
        if no_obs and max_valids is not None:
            continue

        time_series_value['time'] = timestamp_to_iso_string(time[time_index].values)
        if has_count:
            time_series_value['count_tot'] = max_number_of_observations

        time_series.append(time_series_value)

    if max_valids_is_pos:
        time_series = time_series[::-1]

    return time_series


def _to_shapely_geometries(geo_json_geometries: List[GeoJsonGeometry]) -> List[shapely.geometry.base.BaseGeometry]:
    geometries = []
    for geo_json_geometry in geo_json_geometries:
        try:
            geometry = shapely.geometry.shape(geo_json_geometry)
        except (TypeError, ValueError) as e:
            raise ServiceBadRequestError("Invalid GeoJSON geometry encountered") from e
        geometries.append(geometry)
    return geometries


def _to_geo_json_geometries(geo_json: GeoJsonObj) -> Tuple[List[GeoJsonGeometry], bool]:
    is_collection = False
    if GeoJSON.is_feature(geo_json):
        geometry = _get_feature_geometry(geo_json)
        geometries = [geometry]
    elif GeoJSON.is_feature_collection(geo_json):
        is_collection = True
        features = GeoJSON.get_feature_collection_features(geo_json)
        geometries = [_get_feature_geometry(feature) for feature in features] if features else []
    elif GeoJSON.is_geometry_collection(geo_json):
        is_collection = True
        geometries = GeoJSON.get_geometry_collection_geometries(geo_json)
    elif GeoJSON.is_geometry(geo_json):
        geometries = [geo_json]
    else:
        raise ServiceBadRequestError("GeoJSON object expected")
    return geometries, is_collection


def _get_feature_geometry(feature: GeoJsonFeature) -> GeoJsonGeometry:
    geometry = GeoJSON.get_feature_geometry(feature)
    if geometry is None or not GeoJSON.is_geometry(geometry):
        raise ServiceBadRequestError("GeoJSON feature without geometry")
    return geometry


def _get_float_value(values: Optional[np.ndarray], index: int) -> Optional[float]:
    if values is None:
        return None
    value = float(values[index])
    return None if np.isnan(value) else value
