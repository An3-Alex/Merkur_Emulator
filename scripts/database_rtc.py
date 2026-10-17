"""Select the board RTC from the module family, independently of build addresses."""
from rtc4543 import DEFAULT_TIME, Rtc4543
from rtc3049 import Rtc3049


def create_rtc(profile, initial_time=DEFAULT_TIME, **kwargs):
    model = Rtc3049 if profile.rtc_model == 'RV3049' else Rtc4543
    return model(initial_time, **kwargs)


def apply_rtc_gpio(rtc, control: int, port: int) -> int:
    # The old board has a WR pin and keeps its original port-only behavior.
    # M202 shares SDI/SDO and changes DATA direction through the control byte.
    if isinstance(rtc, Rtc3049):
        rtc.control_write(control)
    return rtc.port_write(port)
