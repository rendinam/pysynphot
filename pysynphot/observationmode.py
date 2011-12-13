from __future__ import division
## Automatically adapted for numpy.numarray Mar 05, 2007 by

import string
import glob
import re
import os
import warnings
import numpy as N
import pyfits

import spectrum
import units
import locations
from locations import irafconvert, _refTable
import planck
import wavetable
from tables import CompTable, GraphTable


#Flag to control verbosity
DEBUG = False

rootdir = locations.rootdir
datadir = locations.specdir
wavecat = locations.wavecat

#Constants to hold tables.
GRAPHTABLE= ''
GRAPHDICT = {}
COMPTABLE = ''
COMPDICT = {}
THERMTABLE = ''
THERMDICT = {}
HSTAREA = 45238.93416  # cm^2

def _set_default_refdata():
    global GRAPHTABLE, COMPTABLE, THERMTABLE, HSTAREA
    # Component tables are defined here.

    try:
        GRAPHTABLE = _refTable(os.path.join('mtab','*_tmg.fits'))
        COMPTABLE  = _refTable(os.path.join('mtab','*_tmc.fits'))
    except IOError, e:
        GRAPHTABLE = None
        COMPTABLE = None
        warnings.warn("PYSYN_CDBS is undefined; No graph or component tables could be found; functionality will be SEVERELY crippled.",UserWarning)
    try:
        THERMTABLE = _refTable(os.path.join('mtab','*_tmt.fits'))
    except IOError, e:
        THERMTABLE = None
        print "Warning: %s"%str(e)
        print "         No thermal calculations can be performed."

    HSTAREA = 45238.93416  # cm^2

#Do this on import
_set_default_refdata()


def setref(graphtable=None, comptable=None, thermtable=None,
           area=None):
    """provide user access to global reference data.
    Graph/comp/therm table names must be fully specified."""

    global GRAPHTABLE, COMPTABLE, THERMTABLE, HSTAREA, GRAPHDICT, COMPDICT, THERMDICT

    GRAPHDICT = {}
    COMPDICT = {}
    THERMDICT = {}

    #Check for all None, which means reset
    kwds=set([graphtable,comptable,thermtable,area])
    if kwds == set([None]):
        #then we should reset everything.
        _set_default_refdata()
        return

    #Otherwise, check them all separately
    if graphtable is not None:
        GRAPHTABLE = irafconvert(graphtable)

    if comptable is not None:
        COMPTABLE = irafconvert(comptable)

    if thermtable is not None:
        THERMTABLE = irafconvert(thermtable)

    #Area is a bit different:
    if area is not None:
        HSTAREA = area

    #That's it.
    return

def getref():
    """Collects & returns the current refdata as a dictionary"""
    ans=dict(graphtable=GRAPHTABLE,
             comptable=COMPTABLE,
             thermtable=THERMTABLE,
             area=HSTAREA)
    return ans

def showref():
    """Prints the values settable by setref"""
    refdata = getref()
    for k, v in refdata.items():
        print "%10s: %s"%(k,v)

CLEAR = 'clear'

class BaseObservationMode(object):
    ''' Class that handles the graph table, common to both optical and
    thermal obsmodes.
    '''
    def __init__(self, obsmode, method='HSTGraphTable',graphtable=None):
        #Strip "band()" syntax if present
        tmatch=re.search(r'band\((.*?)\)',obsmode,re.IGNORECASE)
        if tmatch:
            obsmode=tmatch.group(1)
        self._obsmode = obsmode

        if graphtable is None:
            graphtable=GRAPHTABLE

        self.area = HSTAREA

        # For sensitivity calculations: 5.03411762e7 is hc in
        # the appropriate units
        self._constant = 5.03411762e7 * self.area
        self.pardict={}

        modes = obsmode.lower().split(',')
        if '#' in obsmode:
            self.modes=[]
            for m in modes:
                if '#' in m:
                    key,val=m.split('#')
                    self.pardict[key]=float(val)
                    self.modes.append("%s#"%key)
                else:
                    self.modes.append(m)
        else:
            self.modes=modes

#        gt = GraphTable(graphtable)
        if graphtable in GRAPHDICT.keys():
            gt = GRAPHDICT[graphtable]
        else:
            gt = GraphTable(graphtable)
            GRAPHDICT[graphtable] = gt

        self.gtname=graphtable

        self.compnames,self.thcompnames = gt.GetComponentsFromGT(self.modes,1)

        self.components = None #Will be filled by subclasses
        self.pixscale = None

        obm=self._obsmode.lower()

        try:
            self.binset = wavetable.wavetable[obm]
        except KeyError,e:
            #If zero candidates were found, that's ok.
            pass
        except ValueError,e:
            #wavetable will raise a ValueError if the key was ambiguous
            print "Warning, %s"%str(e)


    def __str__(self):
        return self._obsmode


    def __len__(self):
        return len(self.components)

    def _getFileNames(self, comptable, compnames):
        files = []
        for compname in compnames:
            if compname not in [None, '', CLEAR]:
                index = N.where(comptable.compnames == compname)
                try:
                    iraffilename = comptable.filenames[index[0][0]]
                    filename = irafconvert(iraffilename)
                    files.append(filename.lstrip())
                except IndexError:
                    raise IndexError("Can't find %s in comptable %s"%(compname,comptable.name))
            else:
                files.append(CLEAR)

        return files

    def GetFileNames(self):
        return self._throughput_filenames

    def showfiles(self):
        """ Duplicate synphot showfiles behavior"""
        for name in self._throughput_filenames:
            if name != 'clear':
                print name

    def bandWave(self):
        """ Return the binned waveset most appropriate for the obsmode,
        as defined by the wavecat.dat file. """

        if self.binset.startswith('('):
            return self._computeBandwave(self.binset)
        else:
            return self._getBandwaveFomFile(self.binset)

    def _computeBandwave(self, coeff):
        (a,b,c,nwave) = self._computeQuadraticCoefficients(coeff)

        result = N.zeros(shape=[nwave,], dtype=N.float64)

        for i in range(nwave):
            result[i] = ((a * i) + b) * i + c

        return result

    def _computeQuadraticCoefficients(self, coeff):

        coefficients = (coeff[1:][:-1]).split(',')

        c0 = float(coefficients[0])
        c1 = float(coefficients[1])
        c2 = (c1 - c0) / 1999.0    # arbitraily copied from synphot....
        #In synphot.countrate/calcstep.x, it was NSPEC-1, where
        #NSPEC was hardcoded to 2000 as the number of bins into
        #which the wavelength set should be divided by default
        c3 = c2
        if len(coefficients) > 2:
            c2 = float(coefficients[2])
            c3 = c2
        if len(coefficients) > 3:
            c3 = float(coefficients[3])

        nwave = int(2.0 * (c1 - c0) / (c3 + c2)) + 1

        c = c0
        b = c2
        a = (c3 * c3 - c2 * c2) / (4.0 * (c1 - c0))

        return (a,b,c,nwave)

    def _getBandwaveFomFile(self, filename):
        name = irafconvert(filename)

        fs = open(name, mode='r')
        lines = fs.readlines()
        fs.close()

        tokens = []
        for line in lines:
            if not line.startswith('#'):
                tokens.append(line)

        return N.float_(tokens)


class ObservationMode(BaseObservationMode):

    def __init__(self, obsmode, method='HSTGraphTable',graphtable=None,
                 comptable=None, component_dict = {}):

        if graphtable is None:
            graphtable=GRAPHTABLE
        if comptable is None:
            comptable=COMPTABLE

        BaseObservationMode.__init__(self, obsmode, method, graphtable)

#        ct = CompTable(comptable)
        if comptable in COMPDICT.keys():
            ct = COMPDICT[comptable]
        else:
            ct = CompTable(comptable)
            COMPDICT[comptable] = ct

        self.ctname = comptable

        self._throughput_filenames = self._getFileNames(ct, self.compnames)

        self.components = self._getOpticalComponents(self._throughput_filenames,
                                                      component_dict)

    def _getOpticalComponents(self, throughput_filenames, component_dict):
        components = []
        for throughput_name in throughput_filenames:
            if throughput_name.endswith('#]'):
                barename,parkey=throughput_name.split('[')
                parkey=parkey[:-2]
            else:
                parkey=None

            if (throughput_name, self.pardict.get(parkey)) in component_dict.keys():
              component = component_dict[(throughput_name, self.pardict.get(parkey))]
            else:
              component = _Component(throughput_name,
                                     interpval=self.pardict.get(parkey))
              component_dict[(throughput_name, self.pardict.get(parkey))] = component

            if not component.isEmpty():
                components.append(component)

        return components

    def Sensitivity(self):
        '''Calculate the sensitivity by combining the throughput curves
        with hc/lambda to convert erg/cm^2/sec/Angstrom to counts/sec.
        Multiplying this by the flux in erg/cm^2/sec/Angstrom will give
        counts/sec/Angstrom'''
        sensitivity = spectrum.TabularSpectralElement()

        product = self._multiplyThroughputs()

        sensitivity._wavetable = product.GetWaveSet()
        sensitivity._throughputtable = product(sensitivity._wavetable) * \
                                      sensitivity._wavetable * self._constant

        return sensitivity

    def Throughput(self):
        '''Throughput returns the TabularSpectralElement obtained by
        multiplying the SpectralElement components together.  Unitless'''
        try:
            throughput = spectrum.TabularSpectralElement()

            product = self._multiplyThroughputs(0)

            throughput._wavetable = product.GetWaveSet()
            throughput._throughputtable = product(throughput._wavetable)
            throughput.waveunits = product.waveunits
            throughput.name='*'.join([str(x) for x in self.components])

##            throughput = throughput.resample(spectrum.default_waveset)

            return throughput

        except IndexError:   # graph table is broken.
            return None


    def _multiplyThroughputs(self, index):
        product = self.components[index].throughput
        if len(self.components) > index:
            for component in self.components[index+1:]:
                if component.throughput != None:
                    product = product * component.throughput
        return product


    def ThermalSpectrum(self):
        try:
            # delegate to subclass.
            thom = _ThermalObservationMode(self._obsmode)
            self.pixscale = thom.pixscale
            return thom._getSpectrum()
        except IndexError:   # graph table is broken.
            raise IndexError("Cannot calculate thermal spectrum; graphtable may be broken")


class _ThermalObservationMode(BaseObservationMode):

    def __init__(self, obsmode, method='HSTGraphTable',graphtable=None,
                 comptable=None, thermtable=None):

        if graphtable is None:
            graphtable = GRAPHTABLE
        if comptable is None:
            comptable = COMPTABLE
        if thermtable is None:
            thermtable = THERMTABLE


        #The constructor of the parent class defines the self.thcompnames
        BaseObservationMode.__init__(self, obsmode, method, graphtable)

        #Check here to see if there are any.
        if set(self.thcompnames).issubset(set(['clear',''])):
            raise NotImplementedError("No thermal support provided for %s"%obsmode)

#        ct = CompTable(comptable)
        if comptable in COMPDICT.keys():
            ct = COMPDICT[comptable]
        else:
            ct = CompTable(comptable)
            COMPDICT[comptable] = ct

        self.ctname=comptable

        throughput_filenames = self._getFileNames(ct, self.compnames)

#        thct = CompTable(thermtable)
        if thermtable in THERMDICT.keys():
            thct = THERMDICT[thermtable]
        else:
            thct = CompTable(thermtable)
            THERMDICT[thermtable] = thct

        self.thname = thermtable

        thermal_filenames = self._getFileNames(thct, self.thcompnames)

        self.components = self._getThermalComponents(throughput_filenames, \
                                                     thermal_filenames)

        self.pixscale = self._getPixelScale()
        self.name = obsmode+" (thermal)"

    def _getPixelScale(self):
        obsmode = self._obsmode.split(',')
        obsmode = str(obsmode[0]) + ',' + str(obsmode[1])

        fname= locations.get_data_filename('detectors.dat')
        fs = open(fname,mode='r')
        lines = fs.readlines()
        fs.close()

        regx = re.compile(r'\S+', re.IGNORECASE)
        for line in lines:
            try:
                tokens = regx.findall(line)
                if tokens[0] == obsmode:
                    break
            except Exception, e:
                raise ValueError("Error processing %s: %s"%(fname,str(e)))

        return float(tokens[1])

    def _getThermalComponents(self, throughput_filenames, thermal_filenames):
        components = []
        for i in range(len(throughput_filenames)):
            throughput_name = throughput_filenames[i]
            thermal_name = thermal_filenames[i]
            if throughput_name.endswith('#]'):
                barename,parkey=throughput_name.split('[')
                parkey=parkey[:-2]
            else:
                parkey=None

            component = _ThermalComponent(throughput_name, thermal_name, \
                                          interpval=self.pardict.get(parkey))
            if not component.isEmpty():
                components.append(component)

        return components

    def _multiplyThroughputs(self):
        ''' Overrides base class in order to deal with opaque components.
        '''
        index = 0
        for component in self.components:
            if component.throughput != None:
                break
            index += 1

        return BaseObservationMode._multiplyThroughputs(self, index)

    def _getSpectrum(self):
        wave=self._getWavesetIntersection()
        sp = spectrum.ArraySourceSpectrum(wave=wave,
                       flux=N.zeros(shape=wave.shape,dtype=N.float64),
                       waveunits='angstrom',
                       fluxunits='photlam',
                       name="%s %s"%(self.name,'ThermalSpectrum'))


        minw = sp._wavetable[0]
        maxw = sp._wavetable[-1]

        for component in self.components:
            # transmissive section
            if component.throughput != None:
                sp = sp * component.throughput

 #               sp = spectrum.trimSpectrum(sp, minw, maxw)

            # thermal section
            if component.emissivity != None:
                bb = self._bb(sp.GetWaveSet(), component.emissivity.temperature)

                sp_comp = component.emissivity.beamFillFactor * bb * \
                          component.emissivity

                sp = sp + sp_comp

                sp = spectrum.trimSpectrum(sp, minw, maxw)

        return sp

    def _getWavesetIntersection(self):
        minw = spectrum.default_waveset[0]
        maxw = spectrum.default_waveset[-1]

        for component in self.components[1:]:
            if component.emissivity != None:
                wave = component.emissivity.GetWaveSet()

                minw = max(minw, wave[0])
                maxw = min(maxw, wave[-1])

        result = self._mergeEmissivityWavesets()

        result = N.compress(result > minw, result)
        result = N.compress(result < maxw, result)

        # intersection with vega spectrum (why???)
        vegasp = spectrum.TabularSourceSpectrum(locations.VegaFile)
        vegaws = vegasp.GetWaveSet()
        result = N.compress(result > vegaws[0], result)
        result = N.compress(result < vegaws[-1], result)

        return result

    def _mergeEmissivityWavesets(self):
        index = 1

        for component in self.components:
            emissivity = component.emissivity
            if emissivity == None:
                index = index + 1
            else:
                result = emissivity.GetWaveSet()
                break;

        for component in self.components[index:]:
            if component.emissivity != None:
                result = spectrum.MergeWaveSets(result, \
                         component.emissivity.GetWaveSet())
        return result

    def _bb(self, wave, temperature):
        sp = spectrum.ArraySourceSpectrum(wave=wave,
                             flux=planck.bb_photlam_arcsec(wave, temperature),
                                          name='planck bb_photlam_arcsec')
        return sp


class _Component(object):
    def __init__(self, throughput_name, interpval):
        self.throughput_name = throughput_name

        self._empty = True

        self.throughput = self._buildThroughput(throughput_name, interpval)
        if self.throughput is not None:
            self.waveunits = self.throughput.waveunits

    def __str__(self):
        return str(self.throughput)

    def _buildThroughput(self, name, interpval):
        if name != CLEAR:
            if interpval is None:
                self._empty = False
                return spectrum.TabularSpectralElement(name)
            else:
                self._empty = False
                return spectrum.InterpolatedSpectralElement(name, interpval)
        else:
            return None

    def isEmpty(self):
        return self._empty


class _ThermalComponent(_Component):

    def __init__(self, throughput_name, thermal_name, interpval):
        self.throughput_name = throughput_name
        self.thermal_name = thermal_name

        self._empty = True

        self.throughput = self._buildThroughput(throughput_name, interpval)

        if thermal_name != CLEAR:
            self._empty = False
            self.emissivity = spectrum.ThermalSpectralElement(thermal_name)
        else:
            self.emissivity = None











