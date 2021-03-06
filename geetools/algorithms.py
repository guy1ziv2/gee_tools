# coding=utf-8
""" Module holding misc algorithms """
import ee
import ee.data
import math
from . import satellite as satmodule

if not ee.data._initialized:
    ee.Initialize()


def pansharpen_kernel(image, pan, rgb=None, kernel=None):
    """
    Compute the per-pixel means of the unsharpened bands
    source: https://gis.stackexchange.com/questions/296615/pansharpen-landsat-mosaic-in-google-earth-engine

    :param pan: the name of the panchromatic band
    :type pan: str
    :param rgb: the red green blue bands
    :type rgb: tuple or list
    :param kernel: the kernel to reduce neighbors
    :type kernel: ee.Kernel
    :rtype: ee.Image
    """
    if not kernel:
        kernel = ee.Kernel.square(90, 'meters')
    if not rgb:
        rgb = ['red', 'green', 'blue']

    if not pan:
        pan = 'pan'

    bgr = image.select(rgb)
    pani = image.select(pan)
    bgr_mean = bgr.reduce('mean').rename('mean')
    # Compute the aggregate mean of the unsharpened bands and the pan band
    mean_values = pani.addBands(bgr_mean).reduceNeighborhood(
        ee.Reducer.mean(),
        kernel
    )
    gain = mean_values.select('mean_mean').divide(
        mean_values.select('{}_mean'.format(pan)))
    return bgr.divide(bgr_mean).multiply(pani).multiply(gain)


def pansharpen_ihs_fusion(image, pan=None, rgb=None):
    """
    HSV-based Pan-Sharpening
    source: https://gis.stackexchange.com/questions/296615/pansharpen-landsat-mosaic-in-google-earth-engine

    :param image:
    :type image: ee.Image
    the name of the panchromatic band
    :type pan: str
    :param rgb: the red green blue bands
    :type rgb: tuple or list
    :rtype: ee.Image
    """
    if not rgb:
        rgb = ['red', 'green', 'blue']

    if not pan:
        pan = 'pan'

    rgb = image.select(rgb)
    pan = image.select(pan)
    # Convert to HSV, swap in the pan band, and convert back to RGB.
    huesat = rgb.rgbToHsv().select('hue', 'saturation')
    upres = ee.Image.cat(huesat, pan).hsvToRgb()
    return image.addBands(upres)


class Landsat(object):

    @staticmethod
    def brdf_correct(image, red='red', green='green', blue='blue', nir='nir',
                     swir1='swir1', swir2='swir2', satellite=None):
        """ Correct Landsat data for BRDF effects using a c-factor.

        D.P. Roy, H.K. Zhang, J. Ju, J.L. Gomez-Dans, P.E. Lewis, C.B. Schaaf,
        Q. Sun, J. Li, H. Huang, V. Kovalskyy, A general method to normalize
        Landsat reflectance data to nadir BRDF adjusted reflectance,
        Remote Sensing of Environment, Volume 176, April 2016, Pages 255-271

        Interpreted and coded here by Daniel Wiell and Erik Lindquist of the
        United Nations Food and Agriculture Organization

        Transcipted to GEE Python API by Rodrigo E. Principe

        If the band names of the passed image are 'blue', 'green', etc,
        those will be used, if not, relations must be indicated in params.

        :param satellite: a Satellite object. The passed image must have the
            same bands as the original satellite.
        :type satellite: satmodule.Satellite
        :rtype: ee.Image
        """
        constants = {'pi': math.pi}

        if satellite:
            sat = satmodule.Satellite(satellite)
            red = sat.bands['red']
            green = sat.bands['green']
            blue = sat.bands['blue']
            nir = sat.bands['nir']
            swir1 = sat.bands['swir1']
            swir2 = sat.bands['swir2']

        ### HELPERS ###
        def merge(o1, o2):
            def addAll(target, toAdd):
                for key, val in toAdd.items():
                    target[key] = val

            result = {}
            addAll(result, o1)
            addAll(result, o2)
            return result

        def format_str(string, args=None):
            args = args if args else {}
            allArgs = merge(constants, args)
            return string.format(**allArgs)

        def toImage(img, band, args=None):
            """
            compute an expression passed in band if it's a str.
            formats the expression in band using format_str if necessary
            :return: one band image
            """
            if isinstance(band, str):
                # print('band:', band)
                if (band.find('.') > -1) \
                        or (band.find(' ') > -1) \
                        or (band.find('{') > -1):
                    # print('formatted:', format_str(band, args), '\n')
                    band = img.expression(format_str(band, args), {'i': img})
                else:
                    band = image.select(band)

            return ee.Image(band)

        def set_name(img, name, toAdd, args=None):
            """
            compute the band (toAdd) with toImage.
            add the band to the passed image and rename it
            """
            toAdd = toImage(img, toAdd, args)
            return img.addBands(toAdd.rename(name), None, True)

        def setIf(img, name, condition=None, trueValue=None, falseValue=None):

            def invertMask(mask):
                # return mask.multiply(-1).add(1)
                return mask.Not()

            condition = toImage(img, condition)
            trueMasked = toImage(img, trueValue).mask(toImage(img, condition))
            falseMasked = toImage(img, falseValue).mask(invertMask(condition))
            value = trueMasked.unmask(falseMasked)
            return set_name(img, name, value)

        def x(point):
            return ee.Number(ee.List(point).get(0))

        def y(point):
            return ee.Number(ee.List(point).get(1))

        def pointBetween(pointA, pointB):
            return ee.Geometry.LineString([pointA, pointB]).centroid() \
                .coordinates()

        def slopeBetween(pointA, pointB):
            return ((y(pointA)).subtract(y(pointB))) \
                .divide((x(pointA)).subtract(x(pointB)))

        def toLine(pointA, pointB):
            return ee.Geometry.LineString([pointA, pointB])

        ### END HELPERS ###

        # image = image.select([red, green, blue, nir, swir1, swir2],
        #                      ['red', 'green', 'blue', 'nir', 'swir1', 'swir2'])

        inputBandNames = image.bandNames()

        coefficientsByBand = {
            blue: {'fiso': 0.0774, 'fgeo': 0.0079, 'fvol': 0.0372},
            green: {'fiso': 0.1306, 'fgeo': 0.0178, 'fvol': 0.0580},
            red: {'fiso': 0.1690, 'fgeo': 0.0227, 'fvol': 0.0574},
            nir: {'fiso': 0.3093, 'fgeo': 0.0330, 'fvol': 0.1535},
            swir1: {'fiso': 0.3430, 'fgeo': 0.0453, 'fvol': 0.1154},
            swir2: {'fiso': 0.2658, 'fgeo': 0.0387, 'fvol': 0.0639}
        }

        def findCorners(img):
            footprint = ee.Geometry(img.get('system:footprint'))
            bounds = ee.List(footprint.bounds().coordinates().get(0))
            coords = footprint.coordinates()

            def wrap_xs(item):
                return x(item)
            xs = coords.map(wrap_xs)

            def wrap_ys(item):
                return y(item)

            ys = coords.map(wrap_ys)

            def findCorner(targetValue, values):
                def wrap_diff(value):
                    return ee.Number(value).subtract(targetValue).abs()
                diff = values.map(wrap_diff)
                minValue = diff.reduce(ee.Reducer.min())
                idx = diff.indexOf(minValue)
                return ee.Number(coords.get(idx))

            lowerLeft = findCorner(x(bounds.get(0)), xs)
            lowerRight = findCorner(y(bounds.get(1)), ys)
            upperRight = findCorner(x(bounds.get(2)), xs)
            upperLeft = findCorner(y(bounds.get(3)), ys)
            return {
                'upperLeft': upperLeft,
                'upperRight': upperRight,
                'lowerRight': lowerRight,
                'lowerLeft': lowerLeft
            }

        corners = findCorners(image)

        def viewAngles(img):
            maxDistanceToSceneEdge = 1000000
            maxSatelliteZenith = 7.5
            upperCenter = pointBetween(corners['upperLeft'], corners['upperRight'])
            lowerCenter = pointBetween(corners['lowerLeft'], corners['lowerRight'])
            slope = slopeBetween(lowerCenter, upperCenter)
            slopePerp = ee.Number(-1).divide(slope)
            img = set_name(img, 'viewAz',
                             ee.Image(ee.Number(math.pi / 2).subtract((slopePerp).atan())))

            leftLine = toLine(corners['upperLeft'], corners['lowerLeft'])
            rightLine = toLine(corners['upperRight'], corners['lowerRight'])
            leftDistance = ee.FeatureCollection(leftLine) \
                .distance(maxDistanceToSceneEdge)
            rightDistance = ee.FeatureCollection(rightLine) \
                .distance(maxDistanceToSceneEdge)
            viewZenith = rightDistance.multiply(maxSatelliteZenith * 2) \
                .divide(rightDistance.add(leftDistance)) \
                .subtract(maxSatelliteZenith)
            img = set_name(img, 'viewZen',
                             viewZenith.multiply(math.pi).divide(180))

            return img

        image = viewAngles(image)

        def solarPosition(img):
            # Ported from http://pythonfmask.org/en/latest/_modules/fmask/landsatangles.html
            date = ee.Date(ee.Number(img.get('system:time_start')))
            secondsInHour = 3600
            img = set_name(img, 'longDeg',
                           ee.Image.pixelLonLat().select('longitude'))

            img = set_name(img, 'latRad',
                           ee.Image.pixelLonLat().select('latitude') \
                           .multiply(math.pi).divide(180))

            img = set_name(img, 'hourGMT',
                           ee.Number(date.getRelative('second', 'day')).divide(secondsInHour))

            img = set_name(img, 'jdp', # Julian Date Proportion
                           date.getFraction('year'))

            img = set_name(img, 'jdpr', # Julian Date Proportion in Radians
                           'i.jdp * 2 * {pi}')


            img = set_name(img, 'meanSolarTime',
                           'i.hourGMT + i.longDeg / 15')

            img = set_name(img, 'localSolarDiff',
                           '(0.000075 + 0.001868 * cos(i.jdpr) - 0.032077 * sin(i.jdpr)' +
                           '- 0.014615 * cos(2 * i.jdpr) - 0.040849 * sin(2 * i.jdpr))' +
                           '* 12 * 60 / {pi}')

            img = set_name(img, 'trueSolarTime',
                           'i.meanSolarTime + i.localSolarDiff / 60 - 12')

            img = set_name(img, 'angleHour',
                           'i.trueSolarTime * 15 * {pi} / 180')

            img = set_name(img, 'delta',
                           '0.006918'+
                           '- 0.399912 * cos(1 * i.jdpr) + 0.070257 * sin(1 * i.jdpr)'+
                           '- 0.006758 * cos(2 * i.jdpr) + 0.000907 * sin(2 * i.jdpr)'+
                           '- 0.002697 * cos(3 * i.jdpr) + 0.001480 * sin(3 * i.jdpr)')

            img = set_name(img, 'cosSunZen',
                           'sin(i.latRad) * sin(i.delta) ' +
                           '+ cos(i.latRad) * cos(i.delta) * cos(i.angleHour)')

            img = set_name(img, 'sunZen',
                           'acos(i.cosSunZen)')

            img = set_name(img, 'sinSunAzSW',
                           toImage(img, 'cos(i.delta) * sin(i.angleHour) / sin(i.sunZen)') \
                           .clamp(-1, 1))

            img = set_name(img, 'cosSunAzSW',
                           '(-cos(i.latRad) * sin(i.delta)' +
                           '+ sin(i.latRad) * cos(i.delta) * cos(i.angleHour)) / sin(i.sunZen)')

            img = set_name(img, 'sunAzSW',
                           'asin(i.sinSunAzSW)')

            img = setIf(img, 'sunAzSW',
                        'i.cosSunAzSW <= 0',
                        '{pi} - i.sunAzSW',
                        'i.sunAzSW')

            img = setIf(img, 'sunAzSW',
                        'i.cosSunAzSW > 0 and i.sinSunAzSW <= 0',
                        '2 * {pi} + i.sunAzSW',
                        'i.sunAzSW')

            img = set_name(img, 'sunAz',
                           'i.sunAzSW + {pi}')

            img = setIf(img, 'sunAz',
                        'i.sunAz > 2 * {pi}',
                        'i.sunAz - 2 * {pi}',
                        'i.sunAz')

            return img

        image = solarPosition(image)

        def sunZenOut(img):
            # https://nex.nasa.gov/nex/static/media/publication/HLS.v1.0.UserGuide.pdf
            img = set_name(img, 'centerLat',
                             ee.Number(
                                 ee.Geometry(img.get('system:footprint')) \
                                     .bounds().centroid(30).coordinates().get(0)) \
                             .multiply(math.pi).divide(180))
            img = set_name(img, 'sunZenOut',
                             '(31.0076' +
                             '- 0.1272 * i.centerLat' +
                             '+ 0.01187 * pow(i.centerLat, 2)' +
                             '+ 2.40E-05 * pow(i.centerLat, 3)' +
                             '- 9.48E-07 * pow(i.centerLat, 4)' +
                             '- 1.95E-09 * pow(i.centerLat, 5)' +
                             '+ 6.15E-11 * pow(i.centerLat, 6)) * {pi}/180')

            return img

        image = sunZenOut(image)
        image = set_name(image, 'relativeSunViewAz', 'i.sunAz - i.viewAz')

        def cosPhaseAngle(img, name, sunZen, viewZen, relativeSunViewAz):
            args = {
                'sunZen': sunZen,
                'viewZen': viewZen,
                'relativeSunViewAz': relativeSunViewAz}

            img = set_name(img, name,
                             toImage(img, 'cos({sunZen}) * cos({viewZen})' +
                                     '+ sin({sunZen}) * sin({viewZen}) * cos({relativeSunViewAz})',
                                     args).clamp(-1, 1))
            return img

        def rossThick(img, bandName, sunZen, viewZen, relativeSunViewAz):
            args = {'sunZen': sunZen,
                    'viewZen': viewZen,
                    'relativeSunViewAz': relativeSunViewAz}

            img = cosPhaseAngle(img, 'cosPhaseAngle', sunZen, viewZen,
                                  relativeSunViewAz)
            img = set_name(img, 'phaseAngle',
                             'acos(i.cosPhaseAngle)')

            img = set_name(img, bandName,
                             '(({pi}/2 - i.phaseAngle) * i.cosPhaseAngle + sin(i.phaseAngle)) ' +
                             '/ (cos({sunZen}) + cos({viewZen})) - {pi}/4', args)

            return img

        image = rossThick(image, 'kvol', 'i.sunZen', 'i.viewZen', 'i.relativeSunViewAz')
        image = rossThick(image, 'kvol0', 'i.sunZenOut', 0, 0)

        def anglePrime(img, name, angle):
            args = {'b/r': 1, 'angle': angle}
            img = set_name(img, 'tanAnglePrime',
                             '{b/r} * tan({angle})', args)
            img = setIf(img, 'tanAnglePrime', 'i.tanAnglePrime < 0', 0)
            img = set_name(img, name,
                             'atan(i.tanAnglePrime)')
            return img

        def liThin(img, bandName, sunZen, viewZen, relativeSunViewAz):
            # From https://modis.gsfc.nasa.gov/data/atbd/atbd_mod09.pdf
            args = {
                'sunZen': sunZen,
                'viewZen': viewZen,
                'relativeSunViewAz': relativeSunViewAz,
                'h/b': 2,
            }

            img = anglePrime(img, 'sunZenPrime', sunZen)
            img = anglePrime(img, 'viewZenPrime', viewZen)
            img = cosPhaseAngle(img, 'cosPhaseAnglePrime', 'i.sunZenPrime',
                                  'i.viewZenPrime', relativeSunViewAz)
            img = set_name(img, 'distance',
                             'sqrt(pow(tan(i.sunZenPrime), 2) + pow(tan(i.viewZenPrime), 2)' +
                             '- 2 * tan(i.sunZenPrime) * tan(i.viewZenPrime)'+
                             '* cos({relativeSunViewAz}))',
                             args)
            img = set_name(img, 'temp',
                             '1/cos(i.sunZenPrime) + 1/cos(i.viewZenPrime)')
            img = set_name(img, 'cosT',
                             toImage(img, '{h/b} * sqrt(pow(i.distance, 2)'+
                                     '+ pow(tan(i.sunZenPrime) * tan(i.viewZenPrime)'+
                                     '* sin({relativeSunViewAz}), 2))/i.temp', args) \
                             .clamp(-1, 1))
            img = set_name(img, 't', 'acos(i.cosT)')
            img = set_name(img, 'overlap',
                             '(1/{pi}) * (i.t - sin(i.t) * i.cosT) * (i.temp)')
            img = setIf(img, 'overlap', 'i.overlap > 0', 0)
            img = set_name(img, bandName,
                             'i.overlap - i.temp' +
                             '+ (1/2) * (1 + i.cosPhaseAnglePrime) * (1/cos(i.sunZenPrime))'+
                             '* (1/cos(i.viewZenPrime))')

            return img

        image = liThin(image, 'kgeo', 'i.sunZen', 'i.viewZen', 'i.relativeSunViewAz')
        image = liThin(image, 'kgeo0', 'i.sunZenOut', 0, 0)

        def brdf(img, bandName, kvolBand, kgeoBand, coefficients):
            args = merge(coefficients, {
                'kvol': '3 * i.' + kvolBand, # check this multiplication factor. Is there an 'optimal' value?  Without a factor here, there is not enough correction.
                'kgeo': 'i.' + kgeoBand
            })

            img = set_name(img, bandName,
                           '{fiso} + {fvol} * {kvol} + {fgeo} * {kvol}', args)
            return img

        def applyCFactor(img, bandName, coefficients):
            img = brdf(img, 'brdf', 'kvol', 'kgeo', coefficients)
            img = brdf(img, 'brdf0', 'kvol0', 'kgeo0', coefficients)
            img = set_name(img, 'cFactor',
                           'i.brdf0 / i.brdf', coefficients)
            img = set_name(img, bandName,
                           '{bandName} * i.cFactor', {'bandName': 'i.' + bandName})
            return img

        def adjustBands(img):
            """ apply cFactor per band """
            for bandName in coefficientsByBand:
                coefficients = coefficientsByBand[bandName]
                img = applyCFactor(img, bandName, coefficients)
            return img

        image = adjustBands(image)

        return image.select(inputBandNames)
