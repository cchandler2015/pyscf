#!/usr/bin/env python
# Copyright 2014-2020 The PySCF Developers. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Authors: Xing Zhang <zhangxing.nju@gmail.com>
#

import numpy as np
import ctypes
from pyscf import lib
from pyscf.lib import logger
from pyscf import __config__
from pyscf.pbc.symm import symmetry as symm
from pyscf.pbc.lib.kpts_helper import member
from numpy.linalg import inv

KPTS_DIFF_TOL = getattr(__config__, 'pbc_lib_kpts_kpts_diff_tol', 1e-6)
libpbc = lib.load_library('libpbc')

def make_kpts_ibz(kpts):
    '''
    Constructe k-points in IBZ

    Note: 
        This function modifies the :obj:`kpts` object.

    Args:
        kpts : :class:`KPoints` object
    '''
    cell = kpts.cell
    nkpts = kpts.nkpts
    nop = kpts.nop
    op_rot = np.asarray([op.a2b(cell).rot for op in kpts.ops])
    if kpts.time_reversal:
        op_rot = np.concatenate([op_rot, -op_rot])

    bz2bz_ks = map_k_points_fast(kpts.kpts_scaled, op_rot, KPTS_DIFF_TOL)
    kpts.k2opk = bz2bz_ks
    if -1 in bz2bz_ks:
        if kpts.verbose >= logger.WARN:
            logger.warn(kpts, 'k-points have lower symmetry than lattice.')

    bz2bz_k = -np.ones(nkpts+1, dtype=int)
    ibz2bz_k = []
    for k in range(nkpts-1, -1, -1):
        if bz2bz_k[k] == -1:
            bz2bz_k[bz2bz_ks[k]] = k
            ibz2bz_k.append(k)
    ibz2bz_k = np.array(ibz2bz_k[::-1])
    bz2bz_k = bz2bz_k[:-1].copy()

    bz2ibz_k = np.empty(nkpts, int)
    bz2ibz_k[ibz2bz_k] = np.arange(len(ibz2bz_k))
    bz2ibz_k = bz2ibz_k[bz2bz_k]

    kpts.bz2ibz = bz2ibz_k
    kpts.ibz2bz = ibz2bz_k
    kpts.weights_ibz = np.bincount(bz2ibz_k) * (1.0 / nkpts)
    kpts.kpts_scaled_ibz = kpts.kpts_scaled[kpts.ibz2bz]
    kpts.kpts_ibz = kpts.cell.get_abs_kpts(kpts.kpts_scaled_ibz)
    kpts.nkpts_ibz = len(kpts.kpts_ibz)

    for k in range(nkpts):
        bz_k_scaled = kpts.kpts_scaled[k]
        ibz_idx = kpts.bz2ibz[k]
        ibz_k_scaled = kpts.kpts_scaled_ibz[ibz_idx]
        for io, op in enumerate(op_rot):
            if -1 in bz2bz_ks[:,io]: 
                continue
            diff = bz_k_scaled - np.dot(ibz_k_scaled, op.T)
            diff = diff - diff.round()
            if (np.absolute(diff) < KPTS_DIFF_TOL).all():
                kpts.time_reversal_symm_bz[k] = io // nop
                kpts.stars_ops_bz[k] = io % nop
                break

    for i in range(kpts.nkpts_ibz):
        kpts.stars_ops.append([])
        ibz_k_scaled = kpts.kpts_scaled_ibz[i]
        idx = np.where(kpts.bz2ibz == i)[0]
        kpts.stars.append(idx)
        for j in range(idx.size):
            bz_k_scaled = kpts.kpts_scaled[idx[j]]
            for io, op in enumerate(op_rot):
                if -1 in bz2bz_ks[:,io]: 
                    continue
                diff = bz_k_scaled - np.dot(ibz_k_scaled, op.T)
                diff = diff - diff.round()
                if (np.absolute(diff) < KPTS_DIFF_TOL).all():
                    kpts.stars_ops[i].append(io % nop)
                    break

def make_kpairs_ibz(kpts, permutation_symmetry=True):
    #XXX check time-reversal symmetry
    '''
    Constructe k-pairs in IBZ

    Note:
        This function modifies the :obj:`kpts` object.

    Arguments:
        kpts : :class:`KPoints` object
        permutation_symmetry : bool
             Whether to consider permutation symmetry
    '''
    bz2bz_ks = kpts.k2opk
    nop = bz2bz_ks.shape[-1]

    nbzk = kpts.nkpts
    nbzk2 = nbzk*nbzk
    bz2bz_ksks_T = np.empty([nop, nbzk2], dtype=int)
    if kpts.verbose >= logger.INFO:
        logger.info(kpts, 'nkpairs = %s', nbzk2)

    for iop in range(nop):
        tmp = lib.cartesian_prod((bz2bz_ks[:,iop], bz2bz_ks[:,iop]))
        idx_throw = np.unique(np.where(tmp == -1)[0])
        bz2bz_ksks_T[iop] = tmp[:,0] * nbzk + tmp[:,1]
        bz2bz_ksks_T[iop, idx_throw] = -1

    bz2bz_ksks = bz2bz_ksks_T.T
    bz2bz_kk = -np.ones(nbzk2+1, dtype=np.int32)
    ibz2bz_kk = []
    k_group = []
    sym_group = []
    group_size = []
    for k in range(nbzk2-1, -1, -1):
        if bz2bz_kk[k] == -1:
            bz2bz_kk[bz2bz_ksks[k]] = k
            ibz2bz_kk.append(k)
            k_idx, op_idx = np.unique(bz2bz_ksks[k], return_index=True)
            if k_idx[0] == -1:
                k_idx = k_idx[1:]
                op_idx = op_idx[1:]
            group_size.append(op_idx.size)
            k_group.append(k_idx)
            sym_group.append(op_idx)

    ibz2bz_kk = np.array(ibz2bz_kk[::-1])
    kpts.ibz2bz_kk = ibz2bz_kk
    if kpts.verbose >= logger.INFO:
        logger.info(kpts, 'nkpairs_ibz = %s', len(ibz2bz_kk))

    bz2bz_kk = bz2bz_kk[:-1].copy()
    bz2ibz_kk = np.empty(nbzk2,dtype=np.int32)
    bz2ibz_kk[ibz2bz_kk] = np.arange(len(ibz2bz_kk))
    bz2ibz_kk = bz2ibz_kk[bz2bz_kk]

    kpts.bz2ibz_kk = bz2ibz_kk
    kpts.kk_group = k_group[::-1]
    kpts.kk_sym_group = sym_group[::-1]
    kpts.ibz_kk_weight = np.bincount(bz2ibz_kk) *(1.0 / nbzk2)

    if permutation_symmetry:
        idx_i = ibz2bz_kk // nbzk
        idx_j = ibz2bz_kk % nbzk
        idx_ij = np.vstack((idx_i, idx_j))
        idx_ij_sort = np.sort(idx_ij, axis=0)
        _, idx_ij_s2 = np.unique(idx_ij_sort, axis=1, return_index=True)
        ibz2bz_kk_s2 = ibz2bz_kk[idx_ij_s2]
        if kpts.verbose >= logger.INFO:
            logger.info(kpts, 'nkpairs_ibz_s2 = %s', len(ibz2bz_kk_s2))
        kpts.ibz2bz_kk_s2 = ibz2bz_kk_s2
        kpts.ibz_kk_s2_weight = kpts.ibz_kk_weight[idx_ij_s2]

        idx_i = ibz2bz_kk_s2 // nbzk
        idx_j = ibz2bz_kk_s2 % nbzk
        idx_ji = idx_j * nbzk + idx_i
        kpts.ibz_kk_s2_weight[np.where(idx_i != idx_j)[0]]*=2.
        for i in range(len(kpts.ibz_kk_s2_weight)):
            if idx_ji[i] not in kpts.ibz2bz_kk:
                kpts.ibz_kk_s2_weight[i] /= 2.

    #idx_i = (iL2L[L2iL] // nL).reshape((-1,1))
    #idx_j = (iL2L[L2iL] % nL).reshape((-1,1))

    #Lop = np.empty(nL2, dtype = np.int32)
    #Lop[L_group] = sym_group
    #res = np.hstack((iL2L[L2iL].reshape(-1,1), Lop.reshape(-1,1)))
    #buf[idx] = res

    #idx_i = (iL2L // nL).reshape((-1,1))
    #idx_j = (iL2L % nL).reshape((-1,1))
    #idx_ij = np.hstack((idx_i, idx_j))

    #return iL2L, L_group, sym_group, L2iL, idx_ij
    return None

def map_k_points_fast(kpts_scaled, ops, tol=KPTS_DIFF_TOL):
    #This routine is modified from GPAW
    '''
    Find symmetry-related k-points.

    Arguments:
        kpts_scaled : (nkpts, 3) array
            scaled k-points
        ops : (nop, 3, 3) array
            rotation operators
        tol : float
            k-points differ by `tol` are considered as different

    Returns:
        bz2bz_ks : (nkpts, nop) array of int
            mapping table between k and op*k.
            bz2bz_ks[k1,s] = k2 if ops[s] * kpts_scaled[k1] = kpts_scaled[k2] + K,
            where K is a reciprocal lattice vector.
    '''
    nkpts = len(kpts_scaled)
    nop = len(ops)
    bz2bz_ks = -np.ones((nkpts, nop), dtype=int)
    for s, op in enumerate(ops):
        # Find mapped kpoints
        op_kpts_scaled = np.dot(kpts_scaled, op.T)

        # Do some work on the input
        k_kc = np.concatenate([kpts_scaled, op_kpts_scaled])
        k_kc = np.mod(np.mod(k_kc, 1), 1)
        k_kc = aglomerate_points(k_kc, tol)
        k_kc = k_kc.round(-np.log10(tol).astype(int))
        k_kc = np.mod(k_kc, 1)

        # Find the lexicographical order
        order = np.lexsort(k_kc.T)
        k_kc = k_kc[order]
        diff_kc = np.diff(k_kc, axis=0)
        equivalentpairs_k = np.array((diff_kc == 0).all(1), dtype=bool)

        # Mapping array.
        orders = np.array([order[:-1][equivalentpairs_k],
                           order[1:][equivalentpairs_k]])

        # This has to be true.
        assert (orders[0] < nkpts).all()
        assert (orders[1] >= nkpts).all()
        bz2bz_ks[orders[1] - nkpts, s] = orders[0]
    return bz2bz_ks

def aglomerate_points(k_kc, tol=KPTS_DIFF_TOL):
    #This routine is adopted from GPAW
    '''
    Remove numerical error
    '''
    nd = k_kc.shape[1]
    nbzkpts = len(k_kc)

    inds_kc = np.argsort(k_kc, axis=0)

    for c in range(nd):
        sk_k = k_kc[inds_kc[:, c], c]
        dk_k = np.diff(sk_k)

        pt_K = np.argwhere(dk_k > tol)[:, 0]
        pt_K = np.append(np.append(0, pt_K + 1), nbzkpts*2)
        for i in range(len(pt_K) - 1):
            k_kc[inds_kc[pt_K[i]:pt_K[i + 1], c], c] = k_kc[inds_kc[pt_K[i], c], c]
    return k_kc

def symmetrize_density(kpts, rhoR_k, ibz_k_idx, mesh):
    '''
    Transform real-space densities from IBZ to full BZ
    '''
    rhoR_k = np.asarray(rhoR_k, order='C')
    rhoR = np.zeros_like(rhoR_k, order='C')

    dtype = rhoR_k.dtype
    if dtype == np.double:
        symmetrize = libpbc.symmetrize
        symmetrize_ft = libpbc.symmetrize_ft
    elif dtype == np.complex128:
        symmetrize = libpbc.symmetrize_complex
        symmetrize_ft = libpbc.symmetrize_ft_complex
    else:
        raise RuntimeError("Unsupported data type %s" % dtype)

    c_rhoR = rhoR.ctypes.data_as(ctypes.c_void_p)
    c_rhoR_k = rhoR_k.ctypes.data_as(ctypes.c_void_p)

    mesh = np.asarray(mesh, dtype=np.int32, order='C')
    c_mesh = mesh.ctypes.data_as(ctypes.c_void_p)
    for iop in kpts.stars_ops[ibz_k_idx]:
        op = kpts.ops[iop]
        if op.is_eye or op.is_inversion:
            rhoR += rhoR_k
        else:
            inv_op = op.inv()
            op_rot = np.asarray(inv_op.rot, dtype=np.int32, order='C')
            c_op_rot = op_rot.ctypes.data_as(ctypes.c_void_p)
            if inv_op.trans_is_zero:
                symmetrize(c_rhoR, c_rhoR_k, c_op_rot, c_mesh)
            else:
                trans = np.asarray(inv_op.trans, dtype=np.double, order='C')
                c_trans = trans.ctypes.data_as(ctypes.c_void_p)
                symmetrize_ft(c_rhoR, c_rhoR_k, c_op_rot, c_trans, c_mesh)
    return rhoR

def symmetrize_wavefunction(kpts, psiR_k, mesh): 
    #XXX need verification
    '''
    transform real-space wavefunctions from IBZ to full BZ
    '''
    psiR_k = np.asarray(psiR_k, order='C')
    is_complex = psiR_k.dtype == np.complex128
    nao = psiR_k.shape[1]
    nG = psiR_k.shape[2]
    psiR = np.zeros([kpts.nkpts,nao,nG], dtype = psiR_k.dtype, order='C')

    mesh = np.asarray(mesh, dtype=np.int32, order='C')
    c_mesh = mesh.ctypes.data_as(ctypes.c_void_p)

    for ibz_k_idx in range(kpts.nibzk):
        for idx, iop in enumerate(kpts.stars_ops[ibz_k_idx]):
            bz_k_idx = kpts.stars[ibz_k_idx][idx]
            op = symm.transform_rot_b_to_a(kpts.cell, kpts.op_rot[iop])
            op = np.asarray(op, dtype=np.int32, order='C')
            time_reversal = False
            if iop >= kpts.nrot:
                time_reversal = True
                op = -op
            if symm.is_eye(op) or symm.is_inversion(op):
                psiR[bz_k_idx] = psiR_k[ibz_k_idx]
            else:
                c_psiR = psiR[bz_k_idx].ctypes.data_as(ctypes.c_void_p)
                c_psiR_k = psiR_k[ibz_k_idx].ctypes.data_as(ctypes.c_void_p)
                c_op = op.ctypes.data_as(ctypes.c_void_p)
                if is_complex: 
                    libpbc.symmetrize_complex(c_psiR, c_psiR_k, c_op, c_mesh)
                else:
                    libpbc.symmetrize(c_psiR, c_psiR_k, c_op, c_mesh)
    return psiR

def transform_mo_coeff(kpts, mo_coeff_ibz):
    '''
    Transform MO coefficients from IBZ to full BZ

    Arguments:
        kpts : :class:`KPoints` object
        mo_coeff_ibz : ([2,] nkpts_ibz, nao, nmo) array
            MO coefficients for k-points in IBZ
    '''
    mos = []
    is_uhf = False
    if isinstance(mo_coeff_ibz[0][0], np.ndarray) and mo_coeff_ibz[0][0].ndim == 2:
        is_uhf = True
        mos = [[],[]]
    for k in range(kpts.nkpts):
        ibz_k_idx = kpts.bz2ibz[k]
        ibz_k_scaled = kpts.kpts_scaled_ibz[ibz_k_idx]
        iop = kpts.stars_ops_bz[k]
        op = kpts.ops[iop]
        time_reversal = kpts.time_reversal_symm_bz[k]

        def _transform(mo_ibz, iop, op):
            mo_bz = None
            if op.is_eye:
                if time_reversal:
                    mo_bz = mo_ibz.conj()
                else:
                    mo_bz = mo_ibz
            elif op.is_inversion:
                mo_bz = mo_ibz.conj()
            else:
                mo_bz = symm.transform_mo_coeff(kpts.cell, ibz_k_scaled, mo_ibz, op, kpts.Dmats[iop])
                if time_reversal:
                    mo_bz = mo_bz.conj()
            return mo_bz

        if is_uhf:
            mo_coeff_a = mo_coeff_ibz[0][ibz_k_idx]
            mos[0].append(_transform(mo_coeff_a, iop, op))
            mo_coeff_b = mo_coeff_ibz[1][ibz_k_idx]
            mos[1].append(_transform(mo_coeff_b, iop, op))
        else:
            mo_coeff = mo_coeff_ibz[ibz_k_idx]
            mos.append(_transform(mo_coeff, iop, op))
    return mos

def transform_mo_coeff_k(kpts, mo_coeff_ibz, k):
    '''
    Get MO coefficients for a k-point in BZ

    Arguments:
        kpts : :class:`KPoints` object
        mo_coeff_ibz : (nkpts_ibz, nao, nmo) array
            MO coefficients for k-points in IBZ
        k : int
            k-point index in BZ
    '''
    ibz_k_idx = kpts.bz2ibz[k]
    ibz_k_scaled = kpts.kpts_scaled_ibz[ibz_k_idx]
    iop = kpts.stars_ops_bz[k]
    op = kpts.ops[iop]
    time_reversal = kpts.time_reversal_symm_bz[k]

    mo_ibz = mo_coeff_ibz[ibz_k_idx]
    mo_bz = None
    if op.is_eye:
        if time_reversal:
            mo_bz = mo_ibz.conj()
        else:
            mo_bz = mo_ibz
    elif op.is_inversion:
        mo_bz = mo_ibz.conj()
    else:
        mo_bz = symm.transform_mo_coeff(kpts.cell, ibz_k_scaled, mo_ibz, op, kpts.Dmats[iop])
        if time_reversal:
            mo_bz = mo_bz.conj()
    return mo_bz

transform_single_mo_coeff = transform_mo_coeff_k

def transform_mo_occ(kpts, mo_occ_ibz):
    '''
    Transform MO occupations from IBZ to full BZ
    '''
    occ = []
    is_uhf = False
    if isinstance(mo_occ_ibz[0][0], np.ndarray) and mo_occ_ibz[0][0].ndim == 1:
        is_uhf = True
        occ = [[],[]]
    for k in range(kpts.nkpts):
        ibz_k_idx = kpts.bz2ibz[k]
        if is_uhf:
            occ[0].append(mo_occ_ibz[0][ibz_k_idx])
            occ[1].append(mo_occ_ibz[1][ibz_k_idx])
        else:
            occ.append(mo_occ_ibz[ibz_k_idx])
    return occ

def transform_dm(kpts, dm_ibz):
    '''
    Transform density matrices from IBZ to full BZ
    '''
    dms = []
    is_uhf = False
    if (isinstance(dm_ibz, np.ndarray) and dm_ibz.ndim == 4) or \
       (isinstance(dm_ibz[0][0], np.ndarray) and dm_ibz[0][0].ndim == 2):
        is_uhf = True
        dms = [[],[]]
    for k in range(kpts.nkpts):
        ibz_k_idx = kpts.bz2ibz[k]
        ibz_kpt_scaled = kpts.kpts_scaled_ibz[ibz_k_idx]
        iop = kpts.stars_ops_bz[k]
        op = kpts.ops[iop]
        time_reversal = kpts.time_reversal_symm_bz[k]

        def _transform(dm_ibz, iop, op):
            if op.is_eye:
                if time_reversal:
                    dm_bz = dm_ibz.conj()
                else:
                    dm_bz = dm_ibz
            elif op.is_inversion:
                dm_bz = dm_ibz.conj()
            else:
                dm_bz = symm.transform_dm(kpts.cell, ibz_kpt_scaled, dm_ibz, op, kpts.Dmats[iop])
                if time_reversal:
                    dm_bz = dm_bz.conj()
            return dm_bz

        if is_uhf:
            dm_a = dm_ibz[0][ibz_k_idx]
            dms[0].append(_transform(dm_a, iop, op))
            dm_b = dm_ibz[1][ibz_k_idx]
            dms[1].append(_transform(dm_b, iop, op))
        else:
            dm = dm_ibz[ibz_k_idx]
            dms.append(_transform(dm, iop, op))
    if is_uhf:
        nkpts = len(dms[0])
        nao = dms[0][0].shape[0]
        return lib.asarray(dms).reshape(2,nkpts,nao,nao)
    else:
        return lib.asarray(dms)

def transform_mo_energy(kpts, mo_energy_ibz):
    '''
    Transform mo_energy from IBZ to full BZ
    '''
    is_uhf = False
    if isinstance(mo_energy_ibz[0][0], np.ndarray):
        is_uhf = True
    mo_energy_bz = []
    if is_uhf:
        mo_energy_bz = [[],[]]
    for k in range(kpts.nkpts):
        ibz_k_idx = kpts.bz2ibz[k]
        if is_uhf:
            mo_energy_bz[0].append(mo_energy_ibz[0][ibz_k_idx])
            mo_energy_bz[1].append(mo_energy_ibz[1][ibz_k_idx])
        else: 
            mo_energy_bz.append(mo_energy_ibz[ibz_k_idx])
    return mo_energy_bz

def check_mo_occ_symmetry(kpts, mo_occ, tol=1e-6):
    '''
    Check if mo_occ has the correct symmetry
    '''
    for bz_k in kpts.stars:
        nbzk = len(bz_k)
        for i in range(nbzk):
            for j in range(i+1,nbzk):
                if not (np.absolute(mo_occ[bz_k[i]] - mo_occ[bz_k[j]]) < tol).all():
                    raise RuntimeError("Symmetry broken")
    mo_occ_ibz = []
    for k in range(kpts.nkpts_ibz):
        mo_occ_ibz.append(mo_occ[kpts.ibz2bz[k]])
    return mo_occ_ibz

def make_kpts(cell, kpts=np.zeros((1,3)), 
              space_group_symmetry=True, time_reversal_symmetry=True,
              symmorphic=True):
    if isinstance(kpts, KPoints):
        return kpts.build(space_group_symmetry, time_reversal_symmetry, symmorphic)
    else:
        return KPoints(cell, kpts).build(space_group_symmetry, time_reversal_symmetry, symmorphic)

class KPoints(symm.Symmetry, lib.StreamObject):
    '''
    The class handling k-point symmetry.

    Attributes:
        cell : :class:`Cell` object
        verbose : int
            Print level. Default value is `cell.verbose`.
        time_reversal : bool
            Whether to consider time-reversal symmetry
        kpts : (nkpts,3) array
            k-points in full BZ
        kpts_scaled : (nkpts,3) array
            scaled k-points in full BZ
        weights : (nkpts,) array
            weights of k-points in full BZ
        bz2ibz : (nkpts,) array of int
            mapping table from full BZ to IBZ
        kpts_ibz : (nkpts_ibz,3) array
            k-points in IBZ
        kpts_scaled_ibz : (nkpts_ibz,3) array
            scaled k-points in IBZ
        weights_ibz : (nkpts_ibz,) array
            weights of k-points in IBZ
        ibz2bz : (nkpts_ibz,) array of int
            mapping table from IBZ to full BZ
        k2opk (bz2bz_ks) : (nkpts, nop*(time_reversal+1)) array of int
            mapping table between kpts and ops.rot * kpts
        stars : list of (nk,) arrays of int with len(stars)=nkpts_ibz and nk=No. of symmetry-related k-points
            stars of k-points in full BZ
        stars_ops (sym_group) : same as `stars`
            indices of rotation operators connecting k points in full BZ with corresponding IBZ k
        stars_ops_bz (sym_conn) : (nkpts,) array of int
            same as stars_ops but arranged in the sequence of k-points in full BZ
        time_reversal_symm_bz : (nkpts,) array of int
            whether k-points in BZ and IBZ are related by time-reversal symmetry
    '''
    def __init__(self, cell=None, kpts=np.zeros((1,3))): 
        symm.Symmetry.__init__(self, cell)
        self.verbose = logger.NOTE
        if getattr(self.cell, 'verbose', None):
            self.verbose = self.cell.verbose
        self.time_reversal = False

        self.kpts_ibz = self.kpts = kpts
        self.kpts_scaled_ibz = self.kpts_scaled = None
        nkpts = len(self.kpts)
        self.weights_ibz = self.weights = np.asarray([1./nkpts] * nkpts)
        self.ibz2bz = self.bz2ibz = np.arange(nkpts, dtype=int)

        self.k2opk = None
        self.stars = []
        self.stars_ops = []
        self.stars_ops_bz = np.zeros(nkpts, dtype=int)
        self.time_reversal_symm_bz = np.zeros(nkpts, dtype=int)

        #private variables
        self._nkpts = len(self.kpts)
        self._nkpts_ibz = len(self.kpts_ibz)

        #XXX k-pairs
        self.ibz2bz_kk = None
        self.ibz_kk_weight = None
        self.ibz2bz_kk_s2 = None
        self.ibz_kk_s2_weight = None
        self.bz2ibz_kk = None
        self.kk_group = None
        self.kk_sym_group = None

    @property
    def nkpts(self):
        return self._nkpts

    @nkpts.setter
    def nkpts(self, n):
        self._nkpts = n

    @property
    def nkpts_ibz(self):
        return self._nkpts_ibz

    @nkpts_ibz.setter
    def nkpts_ibz(self, n):
        self._nkpts_ibz = n

    def build(self, space_group_symmetry=True, time_reversal_symmetry=True,
              symmorphic=True, make_kpairs=True, *args, **kwargs):
        symm.Symmetry.build(self, space_group_symmetry, symmorphic, *args, **kwargs)
        if not getattr(self.cell, '_built', None): return

        self.time_reversal = time_reversal_symmetry and not self.has_inversion
        self.kpts_scaled_ibz = self.kpts_scaled = self.cell.get_scaled_kpts(self.kpts)
        self.make_kpts_ibz()
        self.dump_info()
        if make_kpairs:
            self.make_kpairs_ibz()
        return self

    def dump_info(self):
        if self.verbose >= logger.INFO:
            logger.info(self, 'time reversal: %s', self.time_reversal)
            logger.info(self, 'k-points in IBZ                           weights')
            for k in range(self.nkpts_ibz):
                logger.info(self, '%d:  %11.8f, %11.8f, %11.8f    %d/%d', 
                            k, *self.kpts_scaled_ibz[k], np.floor(self.weights_ibz[k]*self.nkpts), self.nkpts)

    def make_gdf_kptij_lst_jk(self):
        '''
        Build GDF k-point-pair list for get_jk
        All combinations:
            k_ibz != k_bz
            k_bz  == k_bz
        '''
        kptij_lst = [(self.kpts[i], self.kpts[i]) for i in range(self.nkpts)]
        for i in range(self.nkpts_ibz):
            ki = self.kpts_ibz[i]
            where = member(ki, self.kpts)
            for j in range(self.nkpts): 
                kj = self.kpts[j]
                if not j in where:
                    kptij_lst.extend([(ki,kj)])
        kptij_lst = np.asarray(kptij_lst)
        return kptij_lst

    make_kpts_ibz = make_kpts_ibz
    make_kpairs_ibz = make_kpairs_ibz
    symmetrize_density = symmetrize_density
    symmetrize_wavefunction = symmetrize_wavefunction
    transform_mo_coeff = transform_mo_coeff
    transform_single_mo_coeff = transform_single_mo_coeff
    transform_dm = transform_dm
    transform_mo_energy = transform_mo_energy
    transform_mo_occ = transform_mo_occ
    check_mo_occ_symmetry = check_mo_occ_symmetry


if __name__ == "__main__":
    import numpy
    from pyscf.pbc import gto
    cell = gto.Cell()
    cell.atom = """
        Si  0.0 0.0 0.0
        Si  1.3467560987 1.3467560987 1.3467560987
    """
    cell.a = [[0.0, 2.6935121974, 2.6935121974], 
              [2.6935121974, 0.0, 2.6935121974], 
              [2.6935121974, 2.6935121974, 0.0]]
    cell.verbose = 4
    cell.build()
    nk = [3,3,3]
    kpts_bz = cell.make_kpts(nk)
    kpts0 = cell.make_kpts(nk, space_group_symmetry=True, time_reversal_symmetry=True)
    kpts1 = KPoints(cell, kpts_bz).build(space_group_symmetry=True, time_reversal_symmetry=True)
    print(numpy.allclose(kpts0.kpts_ibz, kpts1.kpts_ibz))

    kpts = KPoints()
    print(kpts.kpts)
