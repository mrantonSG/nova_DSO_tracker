"""Minimal HEALPix NESTED geometry for offline-sky tile enumeration.

Nova has no HEALPix dependency (neither ``astropy-healpix`` nor ``healpy`` is
installed), so the small slice of HEALPix the offline save/base jobs need -- the
nested pixel index <-> (face, x, y) decomposition and the on-sky pixel centre --
is ported here from the reference HEALPix / chealpix implementation (GPL-2,
K. Gorski et al.). HiPS tile numbering (``Norder`` / ``Npix``) uses exactly this
nested ordering, so a pixel's ``Npix`` at an order is its nested index.

Only the NESTED scheme is implemented: the save/base jobs enumerate nested
indices, and the /hips/ routes address tiles by ``Npix`` (nested within an
order). There is no RING code here and none is needed.

The reference values used to verify this port (see tests/test_hips_healpix.py)
come from the healpy project's own docstrings:

    pix2ang([1, 2, 4, 8], ring 11, lonlat=True)
        == (lon=[315, 337.5, 337.5, 337.5],
            lat=[-41.8103149, 41.8103149, 66.44353569, 78.28414761])
    ring2nest([1, 2, 4, 8], 11) == [11, 13, 61, 253]
    max_pixrad(1)  == 0.84106867056793
    max_pixrad(16) == 0.06601476143251
"""

import math

_PI = math.pi
_HALF_PI = math.pi / 2.0

# Face grid offsets (chealpix.c jrll / jpll): face number -> ring/phi anchor.
_JRLL = (2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4)
_JPLL = (1, 3, 5, 7, 0, 2, 4, 6, 1, 3, 5, 7)


def order_to_nside(order: int) -> int:
    return 1 << order


def nside_to_order(nside: int) -> int:
    return nside.bit_length() - 1


def npix_order(order: int) -> int:
    """Total number of pixels (tiles) at an order: 12 * 4**order."""
    return 12 * (4 ** order)


def nest2xyf(nside: int, pix: int):
    """Nested pixel index -> (face, x, y) at ``nside``.

    Port of chealpix ``nest2xyf``. The local index within a face is the
    bit-interleaved (x, y): each base-4 digit's low bit is the x bit, its high
    bit the y bit.
    """
    npface = nside * nside
    face = pix // npface
    local = pix % npface
    ix = 0
    iy = 0
    scale = 1
    while local:
        d = local & 3
        ix += (d & 1) * scale
        iy += ((d >> 1) & 1) * scale
        local >>= 2
        scale <<= 1
    return face, ix, iy


def pix2ang_nest(nside: int, pix: int):
    """Nested pixel index -> (theta, phi) centre in radians.

    Port of chealpix ``pix2ang_nest_z_phi`` (which returns z, phi; theta is
    ``acos(z)``). theta is the colatitude in [0, pi], phi the longitude in
    [0, 2*pi).
    """
    npix = 12 * nside * nside
    fact2 = 4.0 / npix
    nl4 = 4 * nside
    face, ix, iy = nest2xyf(nside, pix)
    jr = _JRLL[face] * nside - ix - iy - 1
    if jr < nside:
        nr = jr
        z = 1.0 - nr * nr * fact2
        kshift = 0
    elif jr > 3 * nside:
        nr = nl4 - jr
        z = nr * nr * fact2 - 1.0
        kshift = 0
    else:
        fact1 = (nside << 1) * fact2
        nr = nside
        z = (2 * nside - jr) * fact1
        kshift = (jr - nside) & 1
    jp = (_JPLL[face] * nr + ix - iy + 1 + kshift) // 2
    if jp > nl4:
        jp -= nl4
    if jp < 1:
        jp += nl4
    phi = (jp - (kshift + 1) * 0.5) * (_HALF_PI / nr)
    return math.acos(max(-1.0, min(1.0, z))), phi


def xyf2nest(nside: int, ix: int, iy: int, face: int) -> int:
    """(face, x, y) -> nested pixel index; the inverse of ``nest2xyf``."""
    local = 0
    bit = 0
    while (ix >> bit) or (iy >> bit):
        local |= ((ix >> bit) & 1) << (2 * bit)
        local |= ((iy >> bit) & 1) << (2 * bit + 1)
        bit += 1
    return face * nside * nside + local


def ang2pix_nest(nside: int, theta: float, phi: float) -> int:
    """(theta, phi) in radians -> the nested pixel that contains the point.

    Port of chealpix ``ang2pix_nest_z_phi``; theta is the colatitude, phi the
    longitude (any value, taken modulo 2*pi).
    """
    z = math.cos(theta)
    za = abs(z)
    tt = (phi % (2.0 * _PI)) / _HALF_PI          # in [0, 4)
    if za <= 2.0 / 3.0:                          # equatorial region
        temp1 = nside * (0.5 + tt)
        temp2 = nside * (z * 0.75)
        jp = int(temp1 - temp2)                  # ascending edge line index
        jm = int(temp1 + temp2)                  # descending edge line index
        ifp = jp // nside
        ifm = jm // nside
        if ifp == ifm:
            face = ifp | 4
        elif ifp < ifm:
            face = ifp
        else:
            face = ifm + 8
        ix = jm & (nside - 1)
        iy = nside - (jp & (nside - 1)) - 1
    else:                                        # polar caps
        ntt = min(int(tt), 3)
        tp = tt - ntt
        tmp = nside * math.sqrt(3.0 * (1.0 - za))
        jp = min(int(tp * tmp), nside - 1)
        jm = min(int((1.0 - tp) * tmp), nside - 1)
        if z >= 0:
            face, ix, iy = ntt, nside - jm - 1, nside - jp - 1
        else:
            face, ix, iy = ntt + 8, jp, jm
    return xyf2nest(nside, ix, iy, face)


def radec_to_nest(order: int, ra_deg: float, dec_deg: float) -> int:
    """The HiPS tile (``Npix`` at ``order``) that contains an ICRS position."""
    return ang2pix_nest(order_to_nside(order), math.radians(90.0 - dec_deg),
                        math.radians(ra_deg))


def _vec(z: float, phi: float):
    s = math.sqrt(max(0.0, 1.0 - z * z))
    return (s * math.cos(phi), s * math.sin(phi), z)


def max_pixrad(nside: int) -> float:
    """Maximum centre-to-corner distance of a HEALPix pixel at ``nside`` (rad).

    Port of ``T_Healpix_Base::max_pixrad``: the angular separation of the
    equator-facing corner of a polar-cap pixel from its centre.
    """
    za = 2.0 / 3.0
    phia = _PI / (4.0 * nside)
    t1 = 1.0 - 1.0 / nside
    t1 *= t1
    zb = 1.0 - t1 / 3.0
    return _angle(_vec(za, phia), _vec(zb, 0.0))


def _centre_vec(ra_deg: float, dec_deg: float):
    ra = math.radians(ra_deg)
    dec = math.radians(dec_deg)
    return (math.cos(dec) * math.cos(ra), math.cos(dec) * math.sin(ra), math.sin(dec))


def _pix_centre_vec(nside: int, pix: int):
    theta, phi = pix2ang_nest(nside, pix)
    z = math.cos(theta)
    s = math.sin(theta)
    return (s * math.cos(phi), s * math.sin(phi), z)


def _angle(a, b):
    d = a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
    return math.acos(max(-1.0, min(1.0, d)))


def cone_pixels(ra_deg: float, dec_deg: float, radius_deg: float,
                min_order: int, max_order: int) -> dict:
    """Nested pixel indices per order whose tiles cover (or over-cover) a disc.

    Conservative and safe: a pixel is included -- and, for coarse orders,
    recursed into -- when its centre is within ``radius + max_pixrad(order)`` of
    the disc centre. Over-fetching a few extra tiles is harmless for an offline
    cache; missing one is not. Returns ``{order: [ipix, ...]}`` for every order
    in ``min_order..max_order`` (both inclusive).

    The recursion starts at order 0; children of a nested pixel ``p`` at order
    ``o`` are exactly ``4*p + 0..3`` at order ``o+1``.
    """
    centre = _centre_vec(ra_deg, dec_deg)
    radius_rad = math.radians(radius_deg)
    out = {o: [] for o in range(min_order, max_order + 1)}

    def recurse(order, pix):
        nside = order_to_nside(order)
        half = max_pixrad(nside)
        d = _angle(_pix_centre_vec(nside, pix), centre)
        if d > radius_rad + half:
            return
        if order >= min_order:
            out[order].append(pix)
        if order < max_order:
            base = pix * 4
            recurse(order + 1, base)
            recurse(order + 1, base + 1)
            recurse(order + 1, base + 2)
            recurse(order + 1, base + 3)

    for p in range(12):
        recurse(0, p)
    return out


def all_pixels_for_order(order: int):
    """All nested indices of an order (the whole sky), ascending."""
    return range(npix_order(order))
