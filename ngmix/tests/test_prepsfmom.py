import galsim
import numpy as np
import pytest

from ngmix.prepsfmom import PrePSFGaussMom
from ngmix import Jacobian
from ngmix import Observation


def _report_info(s, arr, mn, err):
    if mn is not None and err is not None:
        print(
            "%s:" % s,
            np.mean(arr), mn, np.mean(arr)/mn - 1,
            np.std(arr), err, np.std(arr)/err - 1,
            flush=True,
        )
    else:
        print(
            "%s:" % s,
            np.mean(arr), None, None,
            np.std(arr), None, None,
            flush=True,
        )


@pytest.mark.parametrize('snr', [1e1, 1e3])
@pytest.mark.parametrize('pixel_scale', [0.125, 0.25])
@pytest.mark.parametrize('fwhm,psf_fwhm', [(0.6, 0.9), (1.5, 0.9)])
@pytest.mark.parametrize('mom_fwhm', [1.2, 1.5, 2.0])
@pytest.mark.parametrize('image_size', [57, 58])
@pytest.mark.parametrize('pad_factor', [2, 1, 1.5])
@pytest.mark.parametrize('psf_trunc_fac', [1e-3, 1e-5])
def test_prepsfmom_gauss(
    pad_factor, image_size, fwhm, psf_fwhm, pixel_scale, snr, psf_trunc_fac,
    mom_fwhm,
):
    rng = np.random.RandomState(seed=100)

    cen = (image_size - 1)/2
    gs_wcs = galsim.ShearWCS(
        pixel_scale, galsim.Shear(g1=-0.1, g2=0.06)).jacobian()
    scale = np.sqrt(gs_wcs.pixelArea())
    shift = rng.uniform(low=-scale/2, high=scale/2, size=2)
    psf_shift = rng.uniform(low=-scale/2, high=scale/2, size=2)
    xy = gs_wcs.toImage(galsim.PositionD(shift))
    psf_xy = gs_wcs.toImage(galsim.PositionD(psf_shift))

    jac = Jacobian(
        y=cen + xy.y, x=cen + xy.x,
        dudx=gs_wcs.dudx, dudy=gs_wcs.dudy,
        dvdx=gs_wcs.dvdx, dvdy=gs_wcs.dvdy)

    psf_jac = Jacobian(
        y=cen + psf_xy.y, x=cen + psf_xy.x,
        dudx=gs_wcs.dudx, dudy=gs_wcs.dudy,
        dvdx=gs_wcs.dvdx, dvdy=gs_wcs.dvdy)

    gal = galsim.Gaussian(
        fwhm=fwhm
    ).shear(
        g1=-0.1, g2=0.1
    ).withFlux(
        400
    ).shift(
        dx=shift[0], dy=shift[1]
    )
    psf = galsim.Gaussian(
        fwhm=psf_fwhm
    ).shear(
        g1=0.3, g2=-0.15
    )
    im = galsim.Convolve([gal, psf]).drawImage(
        nx=image_size,
        ny=image_size,
        wcs=gs_wcs
    ).array
    noise = np.sqrt(np.sum(im**2)) / snr
    wgt = np.ones_like(im) / noise**2

    psf_im = psf.shift(
        dx=psf_shift[0], dy=psf_shift[1]
    ).drawImage(
        nx=image_size,
        ny=image_size,
        wcs=gs_wcs
    ).array

    g1arr = []
    g2arr = []
    Tarr = []
    farr = []
    momarr = []
    snrarr = []
    fitter = PrePSFGaussMom(
        fwhm=mom_fwhm,
        pad_factor=pad_factor,
        psf_trunc_fac=psf_trunc_fac,
    )

    # get true flux
    im_true = gal.drawImage(
        nx=image_size,
        ny=image_size,
        wcs=gs_wcs,
        method='no_pixel').array
    obs = Observation(
        image=im_true,
        jacobian=jac,
    )
    res = fitter.go(obs=obs)
    flux_true = res["flux"]
    T_true = res["T"]
    g1_true = res["e"][0]
    g2_true = res["e"][1]

    for _ in range(100):
        _im = im + (rng.normal(size=im.shape) * noise)
        obs = Observation(
            image=_im,
            weight=wgt,
            jacobian=jac,
            psf=Observation(image=psf_im, jacobian=psf_jac),
        )

        res = fitter.go(obs=obs)
        if res['flags'] == 0:
            _g1, _g2 = res['e'][0], res['e'][1]
            g1arr.append(_g1)
            g2arr.append(_g2)
            Tarr.append(res['T'])
            farr.append(res['flux'])
            snrarr.append(res["flux"] / res["flux_err"])
            momarr.append(res["mom"])

    print("\n")
    _report_info("snr", snrarr, None, None)
    _report_info("flux", farr, flux_true, res["flux_err"])
    _report_info("T", Tarr, T_true, res["T_err"])
    _report_info("g1", g1arr, g1_true, res["e_err"][0])
    _report_info("g2", g2arr, g2_true, res["e_err"][1])
    mom_cov = np.cov(np.array(momarr).T)
    print("mom cov ratio:\n", res["mom_cov"]/mom_cov, flush=True)
    assert np.allclose(np.mean(farr), flux_true, atol=0, rtol=0.1)
    assert np.allclose(np.std(farr), res["flux_err"], atol=0, rtol=0.2)

    # at low SNR we get a lot of problems with division by noisy things
    if snr > 100:
        assert np.allclose(np.mean(Tarr), T_true, atol=0, rtol=0.1)
        assert np.allclose(np.mean(g1arr), g1_true, atol=0, rtol=0.2)
        assert np.allclose(np.mean(g2arr), g2_true, atol=0, rtol=0.2)
