# The MIT License (MIT)
# Copyright (c) 2019 by the xcube development team and contributors
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

import os
import shutil
import warnings
from abc import ABCMeta, abstractmethod
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple, Union, Mapping

import pandas as pd
import s3fs
import urllib3.util
import xarray as xr
import zarr

from xcube.constants import EXTENSION_POINT_DATASET_IOS
from xcube.constants import FORMAT_NAME_CSV, FORMAT_NAME_MEM, FORMAT_NAME_NETCDF4, FORMAT_NAME_ZARR
from xcube.core.timeslice import append_time_slice, insert_time_slice, replace_time_slice
from xcube.core.verify import assert_cube
from xcube.util.plugin import ExtensionComponent, get_extension_registry


def open_cube(input_path: str,
              format_name: str = None,
              **kwargs) -> xr.Dataset:
    """
    Open a xcube dataset from *input_path*.
    If *format* is not provided it will be guessed from *input_path*.
    :param input_path: input path
    :param format_name: format, e.g. "zarr" or "netcdf4"
    :param kwargs: format-specific keyword arguments
    :return: xcube dataset
    """
    return open_dataset(input_path, format_name=format_name, is_cube=True, **kwargs)


def write_cube(cube: xr.Dataset,
               output_path: str,
               format_name: str = None,
               cube_asserted: bool = False,
               **kwargs) -> xr.Dataset:
    """
    Write a xcube dataset to *output_path*.
    If *format* is not provided it will be guessed from *output_path*.
    :param cube: xcube dataset to be written.
    :param output_path: output path
    :param format_name: format, e.g. "zarr" or "netcdf4"
    :param kwargs: format-specific keyword arguments
    :param cube_asserted: If False, *cube* will be verified, otherwise it is expected to be a valid cube.
    :return: xcube dataset *cube*
    """
    if not cube_asserted:
        assert_cube(cube)
    return write_dataset(cube, output_path, format_name=format_name, **kwargs)


def open_dataset(input_path: str,
                 format_name: str = None,
                 is_cube: bool = False,
                 **kwargs) -> xr.Dataset:
    """
    Open a dataset from *input_path*.
    If *format* is not provided it will be guessed from *output_path*.
    :param input_path: input path
    :param format_name: format, e.g. "zarr" or "netcdf4"
    :param is_cube: Whether a ValueError will be raised, if the dataset read from *input_path* is not a xcube dataset.
    :param kwargs: format-specific keyword arguments
    :return: dataset object
    """
    format_name = format_name if format_name else guess_dataset_format(input_path)
    if format_name is None:
        raise ValueError("Unknown input format")
    dataset_io = find_dataset_io(format_name, modes=["r"])
    if dataset_io is None:
        raise ValueError(f"Unknown input format {format_name!r} for {input_path}")
    dataset = dataset_io.read(input_path, **kwargs)
    if is_cube:
        assert_cube(dataset)
    return dataset


def write_dataset(dataset: xr.Dataset,
                  output_path: str,
                  format_name: str = None,
                  **kwargs) -> xr.Dataset:
    """
    Write dataset to *output_path*.
    If *format* is not provided it will be guessed from *output_path*.
    :param dataset: Dataset to be written.
    :param output_path: output path
    :param format_name: format, e.g. "zarr" or "netcdf4"
    :param kwargs: format-specific keyword arguments
    :return: the input dataset
    """
    format_name = format_name if format_name else guess_dataset_format(output_path)
    if format_name is None:
        raise ValueError("Unknown output format")
    dataset_io = find_dataset_io(format_name, modes=["w"])
    if dataset_io is None:
        raise ValueError(f"Unknown output format {format_name!r} for {output_path}")
    dataset_io.write(dataset, output_path, **kwargs)
    return dataset


class DatasetIO(ExtensionComponent, metaclass=ABCMeta):
    """
    An abstract base class that represents dataset input/output.
    :param name: A unique dataset I/O identifier.
    """

    def __init__(self, name: str):
        super().__init__(EXTENSION_POINT_DATASET_IOS, name)

    @property
    def description(self) -> str:
        """
        :return: A description for this input processor
        """
        return self.get_metadata_attr('description', '')

    @property
    def ext(self) -> str:
        """The primary filename extension used by this dataset I/O."""
        return self.get_metadata_attr('ext', '')

    @property
    def modes(self) -> Set[str]:
        """
        A set describing the modes of this dataset I/O.
        Must be one or more of "r" (read), "w" (write), and "a" (append).
        """
        return self.get_metadata_attr('modes', set())

    @abstractmethod
    def fitness(self, path: str, path_type: str = None) -> float:
        """
        Compute a fitness of this dataset I/O in the interval [0 to 1]
        for reading/writing from/to the given *path*.
        :param path: The path or URL.
        :param path_type: Either "file", "dir", "url", or None.
        :return: the chance in range [0 to 1]
        """
        return 0.0

    def read(self, input_path: str, **kwargs) -> xr.Dataset:
        """Read a dataset from *input_path* using format-specific read parameters *kwargs*."""
        raise NotImplementedError()

    def write(self, dataset: xr.Dataset, output_path: str, **kwargs):
        """"Write *dataset* to *output_path* using format-specific write parameters *kwargs*."""
        raise NotImplementedError()

    def append(self, dataset: xr.Dataset, output_path: str, **kwargs):
        """"Append *dataset* to existing *output_path* using format-specific write parameters *kwargs*."""
        raise NotImplementedError()

    def insert(self, dataset: xr.Dataset, index: int, output_path: str, **kwargs):
        """"Insert *dataset* at *index* into existing *output_path* using format-specific write parameters *kwargs*."""
        raise NotImplementedError()

    def replace(self, dataset: xr.Dataset, index: int, output_path: str, **kwargs):
        """"Replace *dataset* at *index* in existing *output_path* using format-specific write parameters *kwargs*."""
        raise NotImplementedError()

    def update(self, output_path: str, global_attrs: Dict[str, Any] = None, **kwargs):
        """"Update *dataset* at *output_path* using format-specific open parameters *kwargs*."""
        raise NotImplementedError()


def get_extension(name: str):
    return get_extension_registry().get_extension(EXTENSION_POINT_DATASET_IOS, name)


def find_dataset_io_by_name(name: str):
    extension = get_extension(name)
    if not extension:
        return None
    return extension.component


def find_dataset_io(format_name: str, modes: Iterable[str] = None, default: DatasetIO = None) -> Optional[DatasetIO]:
    modes = set(modes) if modes else None
    format_name = format_name.lower()
    dataset_ios = get_extension_registry().find_components(EXTENSION_POINT_DATASET_IOS)
    for dataset_io in dataset_ios:
        # noinspection PyUnresolvedReferences
        if format_name == dataset_io.name.lower():
            # noinspection PyTypeChecker
            if not modes or modes.issubset(dataset_io.modes):
                return dataset_io
    for dataset_io in dataset_ios:
        # noinspection PyUnresolvedReferences
        if format_name == dataset_io.ext.lower():
            # noinspection PyTypeChecker
            if not modes or modes.issubset(dataset_io.modes):
                return dataset_io
    return default


def guess_dataset_format(path: str) -> Optional[str]:
    """
    Guess a dataset format for a file system path or URL given by *path*.
    :param path: A file system path or URL.
    :return: The name of a dataset format guessed from *path*.
    """
    dataset_io_fitness_list = guess_dataset_ios(path)
    if dataset_io_fitness_list:
        return dataset_io_fitness_list[0][0].name
    return None


def guess_dataset_ios(path: str) -> List[Tuple[DatasetIO, float]]:
    """
    Guess suitable DatasetIO objects for a file system path or URL given by *path*.
    Returns a list of (DatasetIO, fitness) tuples, sorted by descending fitness values.
    Fitness values are in the interval (0, 1].
    The first entry is the most appropriate DatasetIO object.
    :param path: A file system path or URL.
    :return: A list of (DatasetIO, fitness) tuples.
    """
    if os.path.isfile(path):
        input_type = "file"
    elif os.path.isdir(path):
        input_type = "dir"
    elif path.find("://") > 0:
        input_type = "url"
    else:
        input_type = None

    dataset_ios = get_extension_registry().find_components(EXTENSION_POINT_DATASET_IOS)

    dataset_io_fitness_list = []
    for dataset_io in dataset_ios:
        fitness = dataset_io.fitness(path, path_type=input_type)
        if fitness > 0.0:
            dataset_io_fitness_list.append((dataset_io, fitness))

    dataset_io_fitness_list.sort(key=lambda item: -item[1])
    return dataset_io_fitness_list


def _get_ext(path: str) -> Optional[str]:
    _, ext = os.path.splitext(path)
    return ext.lower()


def query_dataset_io(filter_fn: Callable[[DatasetIO], bool] = None) -> List[DatasetIO]:
    dataset_ios = get_extension_registry().find_components(EXTENSION_POINT_DATASET_IOS)
    if filter_fn is None:
        return dataset_ios
    return list(filter(filter_fn, dataset_ios))


# noinspection PyAbstractClass
class MemDatasetIO(DatasetIO):
    """
    An in-memory  dataset I/O. Keeps all datasets in a dictionary.
    :param datasets: The initial datasets as a path to dataset mapping.
    """

    def __init__(self, datasets: Dict[str, xr.Dataset] = None):
        super().__init__(FORMAT_NAME_MEM)
        self._datasets = datasets or {}

    @property
    def datasets(self) -> Dict[str, xr.Dataset]:
        return self._datasets

    def fitness(self, path: str, path_type: str = None) -> float:
        if path in self._datasets:
            return 1.0
        ext_value = _get_ext(path) == ".mem"
        type_value = 0.0
        return (3 * ext_value + type_value) / 4

    def read(self, path: str, **kwargs) -> xr.Dataset:
        if path in self._datasets:
            return self._datasets[path]
        raise FileNotFoundError(path)

    def write(self, dataset: xr.Dataset, path: str, **kwargs):
        self._datasets[path] = dataset

    def append(self, dataset: xr.Dataset, path: str, **kwargs):
        if path in self._datasets:
            old_ds = self._datasets[path]
            # noinspection PyTypeChecker
            self._datasets[path] = xr.concat([old_ds, dataset],
                                             dim='time',
                                             data_vars='minimal',
                                             coords='minimal',
                                             compat='equals')
        else:
            self._datasets[path] = dataset.copy()

    def update(self, output_path: str, global_attrs: Dict[str, Any] = None, **kwargs):
        if global_attrs:
            ds = self._datasets[output_path]
            ds.attrs.update(global_attrs)


class Netcdf4DatasetIO(DatasetIO):
    """
    A dataset I/O that reads from / writes to NetCDF files.
    """

    def __init__(self):
        super().__init__(FORMAT_NAME_NETCDF4)

    def fitness(self, path: str, path_type: str = None) -> float:
        ext = _get_ext(path)
        ext_value = ext in {'.nc', '.hdf', '.h5'}
        type_value = 0.0
        if path_type is "file":
            type_value = 1.0
        elif path_type is None:
            type_value = 0.5
        else:
            ext_value = 0.0
        return (3 * ext_value + type_value) / 4

    def read(self, input_path: str, **kwargs) -> xr.Dataset:
        return xr.open_dataset(input_path, **kwargs)

    def write(self, dataset: xr.Dataset, output_path: str, **kwargs):
        dataset.to_netcdf(output_path)

    def append(self, dataset: xr.Dataset, output_path: str, **kwargs):
        import os
        temp_path = output_path + '.temp.nc'
        os.rename(output_path, temp_path)
        old_ds = xr.open_dataset(temp_path)
        new_ds = xr.concat([old_ds, dataset],
                           dim='time',
                           data_vars='minimal',
                           coords='minimal',
                           compat='equals')
        # noinspection PyUnresolvedReferences
        new_ds.to_netcdf(output_path)
        old_ds.close()
        rimraf(temp_path)

    def update(self, output_path: str, global_attrs: Dict[str, Any] = None, **kwargs):
        if global_attrs:
            import netCDF4
            ds = netCDF4.Dataset(output_path, 'r+')
            ds.setncatts(global_attrs)
            ds.close()


class ZarrDatasetIO(DatasetIO):
    """
    A dataset I/O that reads from / writes to Zarr directories or archives.
    """

    def __init__(self):
        super().__init__(FORMAT_NAME_ZARR)

    def fitness(self, path: str, path_type: str = None) -> float:
        ext = _get_ext(path)
        ext_value = 0.0
        type_value = 0.0
        if ext == ".zarr":
            ext_value = 1.0
            if path_type is "dir":
                type_value = 1.0
            elif path_type == "url" or path_type is None:
                type_value = 0.5
            else:
                ext_value = 0.0
        else:
            lower_path = path.lower()
            if lower_path.endswith(".zarr.zip"):
                ext_value = 1.0
                if path_type == "file":
                    type_value = 1.0
                elif path_type is None:
                    type_value = 0.5
                else:
                    ext_value = 0.0
            else:
                if path_type is "dir":
                    type_value = 1.0
                elif path_type == "url":
                    type_value = 0.5
        return (3 * ext_value + type_value) / 4

    def read(self,
             path: str,
             client_kwargs: Dict[str, Any] = None,
             **kwargs) -> xr.Dataset:
        path_or_store = path
        consolidated = False
        if isinstance(path, str):
            path_or_store, consolidated = get_path_or_obs_store(path_or_store, client_kwargs, mode='r')
            if 'max_cache_size' in kwargs:
                max_cache_size = kwargs.pop('max_cache_size')
                if max_cache_size > 0:
                    path_or_store = zarr.LRUStoreCache(path_or_store, max_size=max_cache_size)
        return xr.open_zarr(path_or_store, consolidated=consolidated, **kwargs)

    def write(self,
              dataset: xr.Dataset,
              output_path: str,
              compress: bool = True,
              cname: str = None,
              clevel: int = None,
              shuffle: int = None,
              blocksize: int = None,
              chunksizes: Dict[str, int] = None,
              client_kwargs: Dict[str, Any] = None,
              **kwargs):
        path_or_store, consolidated = get_path_or_obs_store(output_path, client_kwargs, mode='w')
        encoding = self._get_write_encodings(dataset, compress, cname, clevel, shuffle, blocksize, chunksizes)
        dataset.to_zarr(path_or_store, mode='w', encoding=encoding, **kwargs)

    @classmethod
    def _get_write_encodings(cls, dataset, compress, cname, clevel, shuffle, blocksize, chunksizes):
        encoding = None
        if chunksizes:
            encoding = {}
            for var_name in dataset.data_vars:
                var = dataset[var_name]
                chunks: List[int] = []
                for i in range(len(var.dims)):
                    dim_name = var.dims[i]
                    if dim_name in chunksizes:
                        chunks.append(chunksizes[dim_name])
                    else:
                        chunks.append(var.shape[i])
                encoding[var_name] = dict(chunks=chunks)
        if compress:
            blosc_kwargs = dict(cname=cname, clevel=clevel, shuffle=shuffle, blocksize=blocksize)
            for k in list(blosc_kwargs.keys()):
                if blosc_kwargs[k] is None:
                    del blosc_kwargs[k]
            compressor = zarr.Blosc(**blosc_kwargs)

            if encoding:
                for var_name in encoding.keys():
                    encoding[var_name].update(compressor=compressor)
            else:
                encoding = {var_name: dict(compressor=compressor) for var_name in dataset.data_vars}
        return encoding

    def append(self, dataset: xr.Dataset, output_path: str, **kwargs):
        append_time_slice(output_path, dataset)

    def insert(self, dataset: xr.Dataset, index: int, output_path: str, **kwargs):
        insert_time_slice(output_path, index, dataset)

    def replace(self, dataset: xr.Dataset, index: int, output_path: str, **kwargs):
        replace_time_slice(output_path, index, dataset)

    def update(self, output_path: str, global_attrs: Dict[str, Any] = None, **kwargs):
        if global_attrs:
            import zarr
            ds = zarr.open_group(output_path, mode='r+', **kwargs)
            ds.attrs.update(global_attrs)


# noinspection PyAbstractClass
class CsvDatasetIO(DatasetIO):
    """
    A dataset I/O that reads from / writes to CSV files.
    """

    def __init__(self):
        super().__init__(FORMAT_NAME_CSV)

    def fitness(self, path: str, path_type: str = None) -> float:
        if path_type == "dir":
            return 0.0
        ext = _get_ext(path)
        ext_value = {".csv": 1.0, ".txt": 0.5, ".dat": 0.2}.get(ext, 0.1)
        type_value = {"file": 1.0, "url": 0.5, None: 0.5}.get(path_type, 0.0)
        return (3 * ext_value + type_value) / 4

    def read(self, path: str, **kwargs) -> xr.Dataset:
        return xr.Dataset.from_dataframe(pd.read_csv(path, **kwargs))

    def write(self, dataset: xr.Dataset, output_path: str, **kwargs):
        dataset.to_dataframe().to_csv(output_path, **kwargs)


def rimraf(path):
    """
    The UNIX command `rm -rf` for xcube.
    Recursively remove directory or single file.
    :param path:  directory or single file
    """
    if os.path.isdir(path):
        try:
            shutil.rmtree(path, ignore_errors=False)
        except OSError:
            warnings.warn(f"failed to remove file {path}")
    elif os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            warnings.warn(f'failed to remove file {path}')
            pass


def get_path_or_obs_store(path_or_url: str,
                          client_kwargs: Mapping[str, Any] = None,
                          mode: str = 'r') -> Tuple[Union[str, Dict], bool]:
    """
    If *path_or_url* is an object storage URL, return a object storage Zarr store (mapping object)
    using *client_kwargs* and *mode* and a flag indicating whether the Zarr datasets is consolidated.

    Otherwise *path_or_url* is interpreted as a local file system path, retured as-is plus
    a flag indicating whether the Zarr datasets is consolidated.

    :param path_or_url: A path or a URL.
    :param client_kwargs: Object storage client keyword arguments.
    :param mode: "r" or "w"
    :return: A tuple (path_or_obs_store, consolidated).
    """
    if is_obs_url(path_or_url):
        root, obs_fs_kwargs, obs_fs_client_kwargs = parse_obs_url_and_kwargs(path_or_url, client_kwargs)
        s3 = s3fs.S3FileSystem(**obs_fs_kwargs, client_kwargs=obs_fs_client_kwargs)
        consolidated = mode == "r" and s3.exists(f'{root}/.zmetadata')
        return s3fs.S3Map(root=root, s3=s3, check=False, create=mode == "w"), consolidated
    else:
        consolidated = os.path.exists(os.path.join(path_or_url, '.zmetadata'))
        return path_or_url, consolidated


def parse_obs_url_and_kwargs(obs_url: str, obs_kwargs: Mapping[str, Any]) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    """
    Parses *obs_url* and *kwargs* and returns a
    tuple (*root*, *kwargs*, *client_kwargs*) whose elements
    can be passed to the s3fs.S3FileSystem and s3fs.S3Map constructors as follows:::

        obs_fs = s3fs.S3FileSystem(**kwargs, client_kwargs=client_kwargs)
        obs_map = s3fs.S3Map(root=root, s3=obs_fs)

    :param obs_url: Object storage URL, e.g. "s3://bucket/root", or "https://bucket.s3.amazonaws.com/root".
    :param obs_kwargs: Keyword arguments.
    :return: A tuple (root, kwargs, client_kwargs).
    """

    anon = True
    key = None
    secret = None
    client_kwargs = dict(obs_kwargs) if obs_kwargs else dict()

    endpoint_url, root = split_obs_url(obs_url)
    if endpoint_url:
        client_kwargs['endpoint_url'] = endpoint_url

    if 'provider_access_key_id' in client_kwargs:
        key = client_kwargs.pop('provider_access_key_id')
    if 'aws_access_key_id' in client_kwargs:
        key = client_kwargs.pop('aws_access_key_id')
    if 'provider_secret_access_key' in client_kwargs:
        secret = client_kwargs.pop('provider_secret_access_key')
    if 'aws_secret_access_key' in client_kwargs:
        secret = client_kwargs.pop('aws_secret_access_key')
    if key and secret:
        anon = False
    else:
        key = secret = None

    return root, dict(anon=anon, key=key, secret=secret), client_kwargs


def split_obs_url(path: str) -> Tuple[Optional[str], str]:
    """
    If *path* is a URL, return tuple (endpoint_url, root), otherwise (None, *path*)
    """
    url = urllib3.util.parse_url(path)
    if all((url.scheme, url.host, url.path)) and url.scheme != 's3':
        if url.port is not None:
            endpoint_url = f'{url.scheme}://{url.host}:{url.port}'
        else:
            endpoint_url = f'{url.scheme}://{url.host}'
        root = url.path
        if root.startswith('/'):
            root = root[1:]
        return endpoint_url, root
    return None, path


def is_obs_url(path_or_url: str) -> bool:
    """
    Test if *path_or_url* is a potential object storage URL.

    :param path_or_url: Path or URL to test.
    :return: True, if so.
    """
    return path_or_url.startswith("https://") \
           or path_or_url.startswith("http://") \
           or path_or_url.startswith("s3://")
