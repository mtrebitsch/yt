from itertools import groupby

import numpy as np

from yt.geometry.selection_routines import AlwaysSelector
from yt.utilities.io_handler import BaseIOHandler


# http://stackoverflow.com/questions/2361945/detecting-consecutive-integers-in-a-list
def particle_sequences(grids):
    g_iter = sorted(grids, key=lambda g: g.id)
    for _k, g in groupby(enumerate(g_iter), lambda i_x: i_x[0] - i_x[1].id):
        seq = list(v[1] for v in g)
        yield seq[0], seq[-1]


def grid_sequences(grids):
    g_iter = sorted(grids, key=lambda g: g.id)
    for _k, g in groupby(enumerate(g_iter), lambda i_x1: i_x1[0] - i_x1[1].id):
        seq = list(v[1] for v in g)
        yield seq


def determine_particle_fields(handle):
    try:
        particle_fields = [
            s[0].decode("ascii", "ignore").strip() for s in handle["/particle names"][:]
        ]
        _particle_fields = {"particle_" + s: i for i, s in enumerate(particle_fields)}
    except KeyError:
        _particle_fields = {}
    return _particle_fields


class IOHandlerFLASH(BaseIOHandler):
    _particle_reader = False
    _dataset_type = "flash_hdf5"

    def __init__(self, ds):
        super().__init__(ds)
        # Now we cache the particle fields
        self._handle = ds._handle
        self._particle_handle = ds._particle_handle
        self._particle_fields = determine_particle_fields(self._particle_handle)

    def _read_particles(
        self, fields_to_read, type, args, grid_list, count_list, conv_factors
    ):
        pass

    def io_iter(self, chunks, fields):
        f = self._handle
        for chunk in chunks:
            for field in fields:
                # Note that we *prefer* to iterate over the fields on the
                # outside; here, though, we're iterating over them on the
                # inside because we may exhaust our chunks.
                ftype, fname = field
                ds = f[f"/{fname}"]
                for gs in grid_sequences(chunk.objs):
                    start = gs[0].id - gs[0]._id_offset
                    end = gs[-1].id - gs[-1]._id_offset + 1
                    data = ds[start:end, :, :, :]
                    for i, g in enumerate(gs):
                        yield field, g, self._read_obj_field(g, field, (data, i))

    def _read_particle_coords(self, chunks, ptf):
        chunks = list(chunks)
        f_part = self._particle_handle
        p_ind = self.ds.index._particle_indices
        px, py, pz = (self._particle_fields[f"particle_pos{ax}"] for ax in "xyz")
        p_fields = f_part["/tracer particles"]
        assert len(ptf) == 1
        ptype = list(ptf.keys())[0]
        for chunk in chunks:
            start = end = None
            for g1, g2 in particle_sequences(chunk.objs):
                start = p_ind[g1.id - g1._id_offset]
                end = p_ind[g2.id - g2._id_offset + 1]
                x = np.asarray(p_fields[start:end, px], dtype="=f8")
                y = np.asarray(p_fields[start:end, py], dtype="=f8")
                z = np.asarray(p_fields[start:end, pz], dtype="=f8")
                yield ptype, (x, y, z), 0.0

    def _read_particle_fields(self, chunks, ptf, selector):
        chunks = list(chunks)
        f_part = self._particle_handle
        p_ind = self.ds.index._particle_indices
        px, py, pz = (self._particle_fields[f"particle_pos{ax}"] for ax in "xyz")
        p_fields = f_part["/tracer particles"]
        assert len(ptf) == 1
        ptype = list(ptf.keys())[0]
        field_list = ptf[ptype]
        for chunk in chunks:
            for g1, g2 in particle_sequences(chunk.objs):
                start = p_ind[g1.id - g1._id_offset]
                end = p_ind[g2.id - g2._id_offset + 1]
                x = np.asarray(p_fields[start:end, px], dtype="=f8")
                y = np.asarray(p_fields[start:end, py], dtype="=f8")
                z = np.asarray(p_fields[start:end, pz], dtype="=f8")
                mask = selector.select_points(x, y, z, 0.0)
                if mask is None:
                    continue
                for field in field_list:
                    fi = self._particle_fields[field]
                    data = p_fields[start:end, fi]
                    yield (ptype, field), data[mask]

    def _read_obj_field(self, obj, field, ds_offset=None):
        if ds_offset is None:
            ds_offset = (None, -1)
        ds, offset = ds_offset
        # our context here includes datasets and whatnot that are opened in the
        # hdf5 file
        if ds is None:
            ds = self._handle[f"/{field[1]}"]
        if offset == -1:
            data = ds[obj.id - obj._id_offset, :, :, :].transpose()
        else:
            data = ds[offset, :, :, :].transpose()
        return data.astype("=f8")

    def _read_chunk_data(self, chunk, fields):
        f = self._handle
        rv = {}
        for g in chunk.objs:
            rv[g.id] = {}
        # Split into particles and non-particles
        fluid_fields, particle_fields = [], []
        for ftype, fname in fields:
            if ftype in self.ds.particle_types:
                particle_fields.append((ftype, fname))
            else:
                fluid_fields.append((ftype, fname))
        if len(particle_fields) > 0:
            selector = AlwaysSelector(self.ds)
            rv.update(self._read_particle_selection([chunk], selector, particle_fields))
        if len(fluid_fields) == 0:
            return rv
        for field in fluid_fields:
            ftype, fname = field
            ds = f[f"/{fname}"]
            for gs in grid_sequences(chunk.objs):
                start = gs[0].id - gs[0]._id_offset
                end = gs[-1].id - gs[-1]._id_offset + 1
                data = ds[start:end, :, :, :].transpose()
                for i, g in enumerate(gs):
                    rv[g.id][field] = np.asarray(data[..., i], "=f8")
        return rv


class IOHandlerFLASHParticle(BaseIOHandler):
    _particle_reader = True
    _dataset_type = "flash_particle_hdf5"

    def __init__(self, ds):
        super().__init__(ds)
        # Now we cache the particle fields
        self._handle = ds._handle
        self._particle_fields = determine_particle_fields(self._handle)
        self._position_fields = [
            self._particle_fields[f"particle_pos{ax}"] for ax in "xyz"
        ]

    @property
    def chunksize(self):
        return 32**3

    def _read_fluid_selection(self, chunks, selector, fields, size):
        raise NotImplementedError

    def _read_particle_coords(self, chunks, ptf):
        chunks = list(chunks)
        data_files = set()
        assert len(ptf) == 1
        for chunk in chunks:
            for obj in chunk.objs:
                data_files.update(obj.data_files)
        px, py, pz = self._position_fields
        p_fields = self._handle["/tracer particles"]
        for data_file in sorted(data_files, key=lambda x: (x.filename, x.start)):
            pxyz = np.asarray(
                p_fields[data_file.start : data_file.end, [px, py, pz]], dtype="=f8"
            )
            yield "io", pxyz.T, 0.0

    def _yield_coordinates(self, data_file, needed_ptype=None):
        px, py, pz = self._position_fields
        p_fields = self._handle["/tracer particles"]
        pxyz = np.asarray(
            p_fields[data_file.start : data_file.end, [px, py, pz]], dtype="=f8"
        )
        yield ("io", pxyz)

    def _read_particle_data_file(self, data_file, ptf, selector=None):
        px, py, pz = self._position_fields
        p_fields = self._handle["/tracer particles"]
        si, ei = data_file.start, data_file.end

        data_return = {}
        # This should just be a single item
        for ptype, field_list in sorted(ptf.items()):
            x = np.asarray(p_fields[si:ei, px], dtype="=f8")
            y = np.asarray(p_fields[si:ei, py], dtype="=f8")
            z = np.asarray(p_fields[si:ei, pz], dtype="=f8")
            if selector:
                mask = selector.select_points(x, y, z, 0.0)
            del x, y, z
            if mask is None:
                continue

            for field in field_list:
                fi = self._particle_fields[field]
                data = p_fields[si:ei, fi]
                if selector:
                    data = data[mask]
                data_return[(ptype, field)] = data

        return data_return

    def _read_particle_fields(self, chunks, ptf, selector):
        assert len(ptf) == 1
        yield from super()._read_particle_fields(chunks, ptf, selector)

    _pcount = None

    def _count_particles(self, data_file):
        if self._pcount is None:
            self._pcount = self._handle["/localnp"][:].sum()
        si, ei = data_file.start, data_file.end
        pcount = self._pcount
        if None not in (si, ei):
            pcount = np.clip(pcount - si, 0, ei - si)
        return {"io": pcount}

    def _identify_fields(self, data_file):
        fields = [("io", field) for field in self._particle_fields]
        return fields, {}
