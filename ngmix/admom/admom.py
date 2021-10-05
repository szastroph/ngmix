__all__ = ['run_admom', 'find_cen_admom', 'AdmomFitter']

import numpy as np
from numpy import diag

from ..gmix import GMix, GMixModel
from ..moments import fwhm_to_T
from ..shape import e1e2_to_g1g2
from ..observation import Observation
from ..gexceptions import GMixRangeError
from ..util import get_ratio_error

DEFAULT_MAXITER = 200
DEFAULT_SHIFTMAX = 5.0  # pixels
DEFAULT_ETOL = 1.0e-5
DEFAULT_TTOL = 1.0e-3


def run_admom(
    obs, guess,
    maxiter=DEFAULT_MAXITER,
    shiftmax=DEFAULT_SHIFTMAX,
    etol=DEFAULT_ETOL,
    Ttol=DEFAULT_TTOL,
    cenonly=False,
    rng=None,
):
    """
    Run adaptive moments on the observation

    Parameters
    ----------
    obs: Observation
        ngmix.Observation
    guess: ngmix.GMix or a float
        A guess for the fitter.  Can be a full gaussian mixture or a single
        value for T, in which case the rest of the parameters for the
        gaussian are generated.
    maxiter: integer, optional
        Maximum number of iterations, default 200
    etol: float, optional
        absolute tolerance in e1 or e2 to determine convergence,
        default 1.0e-5
    Ttol: float, optional
        relative tolerance in T <x^2> + <y^2> to determine
        convergence, default 1.0e-3
    shiftmax: float, optional
        Largest allowed shift in the centroid, relative to
        the initial guess.  Default 5.0 (5 pixels if the jacobian
        scale is 1)
    cenonly: bool, optional
        If set to True, only fit for the center
    rng: np.random.RandomState
        Random state for creating full gaussian guesses based
        on a T guess

    Returns
    -------
    AdmomResult
    """

    am = AdmomFitter(
        maxiter=maxiter,
        shiftmax=shiftmax,
        etol=etol,
        Ttol=Ttol,
        cenonly=cenonly,
        rng=rng,
    )
    return am.go(obs=obs, guess=guess)


def find_cen_admom(
    obs,
    fwhm=None,
    gmix=None,
    maxiter=DEFAULT_MAXITER,
    shiftmax=DEFAULT_SHIFTMAX,
    etol=DEFAULT_ETOL,
    Ttol=DEFAULT_TTOL,
    ntry=1,
    rng=None,
):
    """
    Use adaptive moments with fixed weight function to find the center

    Parameters
    ----------
    obs: Observation
        ngmix.Observation
    gmix: ngmix.GMix*
        A weight function.  On the first iteration the center of this
        gmix is used for the guess, on subsequent tries a guess is
        generated as a uniform deviate within a pixel scale.
    fwhm: ngmix.GMix*
        The fwhm for a gaussian weight function.  On the first iteration the
        center of the jacobian is used for the guess, on subsequent tries a
        guess is generated as a uniform deviate within a pixel scale.
    maxiter: integer, optional
        Maximum number of iterations, default 200
    etol: float, optional
        absolute tolerance in e1 or e2 to determine convergence,
        default 1.0e-5
    Ttol: float, optional
        relative tolerance in T <x^2> + <y^2> to determine
        convergence, default 1.0e-3
    shiftmax: float, optional
        Largest allowed shift in the centroid, relative to the initial guess.
        Default 5.0 (5 pixels if the jacobian scale is 1)
    rng: np.random.RandomState
        Random state, required if more than one try is requested in order
        to generate guesses.

    Returns
    -------
    AdmomResult with entry "cen" set.  This cen is the offset relative to
    the jacobian center
    """

    if ntry > 1 and rng is None:
        raise ValueError(
            'send a random number generator rng= when trying more than once '
            'this facilitates generating a new guess for the center'
        )

    if gmix is not None:
        wt = gmix.copy()
    elif fwhm is not None:
        T = fwhm_to_T(fwhm)
        pars = [0.0, 0.0, 0.0, 0.0, T, 1.0]
        wt = GMixModel(pars, 'gauss')
    else:
        raise ValueError('send gmix= or fwhm=')

    scale = obs.jacobian.scale

    am = AdmomFitter(
        maxiter=maxiter,
        shiftmax=shiftmax,
        etol=etol,
        Ttol=Ttol,
        cenonly=True,
    )

    for itry in range(ntry):
        res = am.go(obs=obs, guess=wt)
        if res['flags'] == 0:
            break

        if ntry > 1:
            # offset from jacobian center
            drow, dcol = rng.uniform(low=-scale/2, high=scale/2, size=2)
            wt.set_cen(row=drow, col=dcol)

    if res['flags'] == 0:
        res['cen'] = res.get_gmix().get_cen()
    else:
        res['cen'] = np.zeros(2) + np.nan

    return res


class AdmomResult(dict):
    """
    Represent a fit using adaptive moments, and generate images and mixtures
    for the best fit

    Parameters
    ----------
    obs: observation(s)
        Observation, ObsList, or MultiBandObsList
    result: dict
        the basic fit result, to bad added to this object's keys
    """

    def __init__(self, obs, result):
        self._obs = obs
        self.update(result)

    def get_gmix(self):
        """
        get a gmix representing the best fit, normalized
        """
        if self['flags'] != 0:
            raise RuntimeError('cannot create gmix, fit failed')

        pars = self['pars'].copy()
        pars[5] = 1.0

        e1 = pars[2]/pars[4]
        e2 = pars[3]/pars[4]

        g1, g2 = e1e2_to_g1g2(e1, e2)
        pars[2] = g1
        pars[3] = g2

        return GMixModel(pars, "gauss")

    def make_image(self):
        """
        Get an image of the best fit mixture

        Returns
        -------
        image: array
            Image of the model, including the PSF if a psf was sent
        """
        if self['flags'] != 0:
            raise RuntimeError('cannot create image, fit failed')

        obs = self._obs
        jac = obs.jacobian

        gm = self.get_gmix()
        gm.set_flux(obs.image.sum())

        im = gm.make_image(
            obs.image.shape,
            jacobian=jac,
        )
        return im


class AdmomFitter(object):
    """
    Measure adaptive moments for the input observation

    parameters
    ----------
    maxiter: integer, optional
        Maximum number of iterations, default 200
    etol: float, optional
        absolute tolerance in e1 or e2 to determine convergence,
        default 1.0e-5
    Ttol: float, optional
        relative tolerance in T <x^2> + <y^2> to determine
        convergence, default 1.0e-3
    shiftmax: float, optional
        Largest allowed shift in the centroid, relative to
        the initial guess.  Default 5.0 (5 pixels if the jacobian
        scale is 1)
    cenonly: bool, optional
        If set to True, only vary the center
    rng: np.random.RandomState
        Random state for creating full gaussian guesses based
        on a T guess
    """

    def __init__(self,
                 maxiter=DEFAULT_MAXITER,
                 shiftmax=DEFAULT_SHIFTMAX,
                 etol=DEFAULT_ETOL,
                 Ttol=DEFAULT_TTOL,
                 cenonly=False,
                 rng=None):

        self._set_conf(
            maxiter=maxiter,
            shiftmax=shiftmax,
            etol=etol,
            Ttol=Ttol,
            cenonly=cenonly,
        )

        self.rng = rng

    def go(self, obs, guess):
        """
        run the adpative moments

        parameters
        ----------
        obs: Observation
            ngmix.Observation
        guess: ngmix.GMix or a float
            A guess for the fitter.  Can be a full gaussian mixture or a single
            value for T, in which case the rest of the parameters for the
            gaussian are generated.
        """
        from .admom_nb import admom

        if not isinstance(obs, Observation):
            raise ValueError("input obs must be an Observation")

        guess_gmix = self._get_guess(obs=obs, guess=guess)

        ares = self._get_am_result()

        wt_gmix = guess_gmix._data
        try:
            admom(
                self.conf,
                wt_gmix,
                obs.pixels,
                ares,
            )
        except GMixRangeError:
            ares['flags'] = 0x8

        result = get_result(ares)

        return AdmomResult(obs=obs, result=result)

    def _get_guess(self, obs, guess):
        if isinstance(guess, GMix):
            guess_gmix = guess
        else:
            Tguess = guess  # noqa
            guess_gmix = self._generate_guess(obs=obs, Tguess=Tguess)
        return guess_gmix

    def _set_conf(self, maxiter, shiftmax, etol, Ttol, cenonly):  # noqa
        dt = np.dtype(_admom_conf_dtype, align=True)
        conf = np.zeros(1, dtype=dt)

        conf['maxit'] = maxiter
        conf['shiftmax'] = shiftmax
        conf['etol'] = etol
        conf['Ttol'] = Ttol
        conf['cenonly'] = cenonly

        self.conf = conf

    def _get_am_result(self):
        dt = np.dtype(_admom_result_dtype, align=True)
        return np.zeros(1, dtype=dt)

    def _get_rng(self):
        if self.rng is None:
            self.rng = np.random.RandomState()

        return self.rng

    def _generate_guess(self, obs, Tguess):  # noqa

        rng = self._get_rng()

        scale = obs.jacobian.get_scale()
        pars = np.zeros(6)
        pars[0:0+2] = rng.uniform(low=-0.5*scale, high=0.5*scale, size=2)
        pars[2:2+2] = rng.uniform(low=-0.3, high=0.3, size=2)
        pars[4] = Tguess*(1.0 + rng.uniform(low=-0.1, high=0.1))
        pars[5] = 1.0

        return GMixModel(pars, "gauss")


def get_result(ares):
    """
    copy the result structure to a dict, and
    calculate a few more things
    """

    if isinstance(ares, np.ndarray):
        ares = ares[0]
        names = ares.dtype.names
    else:
        names = list(ares.keys())

    res = {}
    for n in names:
        if n == 'sums':
            res[n] = ares[n].copy()
        elif n == 'sums_cov':
            res[n] = ares[n].reshape((6, 6)).copy()
        else:
            res[n] = ares[n]

    res['flux_mean'] = -9999.0
    res['s2n'] = -9999.0
    res['e'] = np.array([-9999.0, -9999.0])
    res['e_err'] = 9999.0

    if res['flags'] == 0:
        flux_sum = res['sums'][5]
        res['flux_mean'] = flux_sum/res['wsum']
        res['pars'][5] = res['flux_mean']

        # now want pars and cov for [cen1,cen2,e1,e2,T,flux]
        sums = res['sums']

        pars = res['pars']
        sums_cov = res['sums_cov']

        res['T'] = pars[4]

        if sums[5] > 0.0:
            # the sums include the weight, so need factor of two to correct
            res['T_err'] = 4*get_ratio_error(
                sums[4],
                sums[5],
                sums_cov[4, 4],
                sums_cov[5, 5],
                sums_cov[4, 5],
            )

        if res['T'] > 0.0:
            res['e'][:] = res['pars'][2:2+2]/res['T']

            sums = res['sums']
            res['e1err'] = 2*get_ratio_error(
                sums[2],
                sums[4],
                sums_cov[2, 2],
                sums_cov[4, 4],
                sums_cov[2, 4],
            )
            res['e2err'] = 2*get_ratio_error(
                sums[3],
                sums[4],
                sums_cov[3, 3],
                sums_cov[4, 4],
                sums_cov[3, 4],
            )

            if (not np.isfinite(res['e1err']) or
                    not np.isfinite(res['e2err'])):
                res['e1err'] = 9999.0
                res['e2err'] = 9999.0
                res['e_cov'] = diag([9999.0, 9999.0])
            else:
                res['e_cov'] = diag([res['e1err']**2, res['e2err']**2])

        else:
            res['flags'] = 0x8

        fvar_sum = sums_cov[5, 5]

        if fvar_sum > 0.0:

            flux_err = np.sqrt(fvar_sum)
            res['s2n'] = flux_sum/flux_err

            # error on each shape component from BJ02 for gaussians
            # assumes round

            res['e_err_r'] = 2.0/res['s2n']
        else:
            res['flags'] = 0x40

    res['flagstr'] = _admom_flagmap[res['flags']]

    return res


_admom_result_dtype = [
    ('flags', 'i4'),
    ('numiter', 'i4'),
    ('nimage', 'i4'),
    ('npix', 'i4'),
    ('wsum', 'f8'),

    ('sums', 'f8', 6),
    ('sums_cov', 'f8', (6, 6)),
    ('pars', 'f8', 6),
    # temporary
    ('F', 'f8', 6),
]

_admom_conf_dtype = [
    ('maxit', 'i4'),
    ('shiftmax', 'f8'),
    ('etol', 'f8'),
    ('Ttol', 'f8'),
    ('cenonly', bool),
]

_admom_flagmap = {
    0: 'ok',
    0x1: 'edge hit',  # not currently used
    0x2: 'center shifted too far',
    0x4: 'flux < 0',
    0x8: 'T < 0',
    0x10: 'determinant near zero',
    0x20: 'maxit reached',
    0x40: 'zero var',
}
