from context import effex as fx

import cupy as cp
import cusignal
import matplotlib.pyplot as plt
import numpy as np
import pytest


cp.random.seed(77777)

# ------------------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------------------
@pytest.fixture(scope='class')
def cor():
    # Class scope cuts down on time spent init-ing SDRs
    # Used by any test function that needs a default correlator instance
    print('New correlator instance')
    cor_fixture = fx.Correlator()
    yield cor_fixture
    cor_fixture.close()


# ------------------------------------------------------------------------------
# Nominal test class: reuses one default-init correlator instance for speed
# ------------------------------------------------------------------------------
@ pytest.mark.usefixtures('cor')
class TestNominal(object):
    # Helpers
    def gen_complex_sinusoid(self, num_samp, rate, freq, noisy=False):
        time = num_samp / rate
        t = cp.linspace(0, time, num=num_samp)
        omega = 2. * cp.pi * freq
        phi = 0.0
        iq = cp.cos(omega * t + phi) + 1j * cp.sin(omega * t + phi)
    
        if noisy:
            iq += self.gen_complex_noise(num_samp, rate, scale=.1)
    
        return iq
    
    
    def gen_complex_noise(self, num_samp, rate, scale=.1):
        time = num_samp / rate
        t = cp.linspace(0, time, num=num_samp)
        iq = cp.random.normal(size=num_samp, scale=scale) + 1j * cp.random.normal(size=num_samp, scale=scale)
    
        return iq


    def step_and_assert(self, cor, sequence):
        # helper for testing correlator state machine
        for state in sequence:
            cor.state = state
            assert(state == cor.state)


    # -------------------------------------------------------------------------
    # Function-level testing
    # -------------------------------------------------------------------------
    @pytest.mark.parametrize('num_samp', [3+2**12, 2**18])
    @pytest.mark.parametrize('rate', [1e6, 2.4e6])
    @pytest.mark.parametrize('freq', [2e4, 1e5])
    @pytest.mark.parametrize('taps', [4, 32])
    @pytest.mark.parametrize('branches', [2048, 4096])
    def test_func_spectrometer_poly(self, cor, num_samp, rate, freq, taps, branches, plot=False):
        # This is to test that the PFB frontend and subsequent methods of
        # generating a power spectrum are valid, by identifying a frequency
        # component of known value.
        iq = self.gen_complex_sinusoid(num_samp, rate, freq, noisy=False)

        window = (cusignal.get_window("hamming", taps * branches)
                * cusignal.firwin(taps * branches, cutoff=1.0/branches, window='rectangular'))    

        spec = cor._spectrometer_poly(iq, taps, branches, window)
    
        psd = cp.real(spec * cp.conj(spec)).mean(axis=0)
    
        freqs = cp.fft.fftshift(cp.fft.fftfreq(len(psd), d=1/rate))
        psd = cp.fft.fftshift(psd)
    
        freq_err_pct = 100. * abs(freqs[cp.argmax(psd)] - freq) / freq
        assert(freq_err_pct < 1.)
    
        if plot:
            ax = plt.axes()
            ax.plot(cp.asnumpy(freqs), cp.asnumpy(psd))
            plt.show()
    
    
    @pytest.mark.parametrize('num_samp', [3+2**12, 2**18])
    @pytest.mark.parametrize('rate', [2.4e6])
    @pytest.mark.parametrize('samp_offset_int', [-2000, -1001, -1, 0, 1, 999, 2000])
    def test_func_estimate_integer_delay(self, cor, num_samp, rate, samp_offset_int):
        # This is to test that integer-sample delay estimation is functioning by
        # artificially applying a known delay and estimating it like we would with
        # no a priori knowledge
        iq_0 = self.gen_complex_noise(num_samp, rate)
        iq_1 = cp.roll(iq_0, samp_offset_int)
    
        est_delay = cor._estimate_integer_delay(iq_0, iq_1, rate)
        est_delay_samples = est_delay * rate
    
        assert(abs(samp_offset_int - est_delay_samples) < 1e-9)
    
    
    # -----------------------------------------------------------------------------
    # System-level testing
    # -----------------------------------------------------------------------------
    def test_correlator_init(self, cor):
        # Test default init
        assert('OFF' == cor.state)
        assert('SPECTRUM' == cor.mode)
        assert(2.4e6 == cor.bandwidth)
        assert(2**12 == cor.nbins)
        assert(1.4204e9 == cor.frequency)
        assert(49.6 == cor.gain)
    
    
    def test_change_bandwidth(self, cor):
        cor.bandwidth = 2.3e6
        assert(2.3e6 == cor.bandwidth)
    
    
    def test_change_nbins(self, cor):
        cor.nbins = 2**11
        assert(2**11 == cor.nbins)
    
    
    def test_change_frequency(self, cor):
        cor.frequency = 1.419e9
        assert(1.419e9 == cor.frequency)
    
    
    def test_change_gain(self, cor):
        cor.gain = 29.7
        assert(29.7 == cor.gain)
    
    
    def test_nominal_state_transitions(self, cor):
        nom_sequence = ('STARTUP', 'RUN', 'CALIBRATE', 'RUN', 'SHUTDOWN', 'OFF')
        self.step_and_assert(cor, nom_sequence)
    
    
    def test_early_aborts(self, cor):
        seq = ('STARTUP', 'SHUTDOWN', 'OFF')
        self.step_and_assert(cor, seq)
        seq = ('STARTUP', 'RUN', 'SHUTDOWN', 'OFF')
        self.step_and_assert(cor, seq)
        seq = ('STARTUP', 'RUN', 'CALIBRATE', 'SHUTDOWN', 'OFF')
        self.step_and_assert(cor, seq)
        seq = ('STARTUP', 'RUN', 'CALIBRATE', 'RUN', 'SHUTDOWN', 'OFF')
        self.step_and_assert(cor, seq)
    
    
    def test_bad_transition_from_OFF(self, cor):
        # Starting in OFF
        with pytest.raises(fx.Correlator.StateTransitionError):
            # already off
            cor.state = 'OFF'
        with pytest.raises(fx.Correlator.StateTransitionError):
            # can't run without starting up first
            cor.state = 'RUN'


    def test_bad_transition_from_STARTUP(self, cor):
        # Starting in STARTUP
        with pytest.raises(fx.Correlator.StateTransitionError):
            cor.state = 'STARTUP'
            # already starting
            cor.state = 'STARTUP'
        cor.state = 'SHUTDOWN'
        cor.state = 'OFF'


    def test_bad_transition_from_RUN(self, cor):
        # Starting in RUN
        with pytest.raises(fx.Correlator.StateTransitionError):
            cor.state = 'STARTUP'
            cor.state = 'RUN'
            # already running
            cor.state = 'RUN'
        with pytest.raises(fx.Correlator.StateTransitionError):
            # already started
            cor.state = 'STARTUP'
        cor.state = 'SHUTDOWN'
        cor.state = 'OFF'


    def test_bad_transition_from_CALIBRATE(self, cor):
        # Starting in CALIBRATE
        with pytest.raises(fx.Correlator.StateTransitionError):
            cor.state = 'STARTUP'
            cor.state = 'RUN'
            cor.state = 'CALIBRATE'
            # already calibrating
            cor.state = 'CALIBRATE'
        with pytest.raises(fx.Correlator.StateTransitionError):
            # already started
            cor.state = 'STARTUP'
        cor.state = 'SHUTDOWN'
        cor.state = 'OFF'
    

# ------------------------------------------------------------------------------
# Off-nominal init tests
# ------------------------------------------------------------------------------
def test_bad_run_time_init():
    with pytest.raises(ValueError):
        bad_cor = fx.Correlator(run_time=0)
        bad_cor.close()


def test_bad_bandwidth_init():
    # Should just print a warning for now
    bad_cor = fx.Correlator(bandwidth=3.0e6)
    bad_cor.close()


def test_bad_mode_init():
    with pytest.raises(ValueError):
        bad_cor = fx.Correlator(mode='FOO')
        bad_cor.close()


def test_alt_mode_init(): 
    # Test alternate mode init
    alt_cor = fx.Correlator(mode='CONTINUUM')
    assert('OFF' == alt_cor.state)
    assert('CONTINUUM' == alt_cor.mode)
    alt_cor.close()


