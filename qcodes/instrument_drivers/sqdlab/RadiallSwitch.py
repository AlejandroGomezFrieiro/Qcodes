from qcodes import (
    VisaInstrument, InstrumentChannel, Parameter, ManualParameter, 
    validators as vals
)

class SwitchPort(Parameter):
    def __init__(self, name, port, reset_pin, set_pin, **kwargs):
        '''
        A single port on a single microwave switch.

        Arguments:
            name: `str`
                Name of the parameter exposed by SwitchChannel.
            port: `str`
                Name of the port on the switch
            reset_pin: `int`
                RPi pin pulsed to open the switch port.
            set_pin: `int`
                RPi pin pulsed to close the switch port.
            **kwargs:
                Passed to base class.
        '''
        super().__init__(name, **kwargs)
        self.port = port
        self.reset_pin = reset_pin
        self.set_pin = set_pin
        # initialize GPIO ports
        for pin in (set_pin, reset_pin):
            self._instrument.outpin(pin, False)
            self._instrument.direction(pin, 'OUT')

    def notify(self, pin):
        '''
        Notify the port that a pin was pulsed on the channel.
        The pin is the set or reset pin, update the internal state of the port.
        '''
        if pin == self.set_pin:
            self.state = True
        if pin == self.reset_pin:
            self.state = False

    def get_raw(self):
        '''Return current state of the port. None indicates an unknown state.'''
        return getattr(self, 'state', None)

    def set_raw(self, state):
        '''Set or reset the port.'''
        self._instrument.pulse(self.set_pin if state else self.reset_pin, True)


class SwitchChannel(InstrumentChannel):
    ROUTE_NONE = 'Disconnected'
    ROUTE_MULTIPLE = 'Multiple'

    def __init__(self, parent, name, portmap):
        '''
        A microwave switch.

        Arguments:
            portmap: `dict` with `str`:`SwitchPort` items
                Port names and state objects of the switch.
        '''
        super().__init__(parent, name)
        self.add_parameter('settle_time', ManualParameter, initial_value=10e-3, 
                           vals=vals.Numbers(0.5e-6, 1.), unit='s')
        self.add_parameter('route', 
            docstring='''
            Set a point-to-point connection on the switch.

            Route can be set to ROUTE_NONE or any port name to reset all ports
            but one and may return ROUTE_NONE, ROUTE_MULTIPLE or any port name.
            '''
            get_cmd=self._get_route, set_cmd=self._set_route,  
            vals=vals.Enum(None, self.ROUTE_NONE, *portmap.keys())
        )
        for port, pins in portmap.items():
            self.add_parameter('{}_state'.format(port), SwitchPort, 
                               port=port, reset_pin=pins[0], set_pin=pins[1], 
                               vals=vals.Enum(True, False, None))

    def _get_route(self):
        '''Calculate route from set ports.'''
        route = self.ROUTE_NONE
        for port in self.parameters.values():
            if isinstance(port, SwitchPort):
                state = port.get()
                if state is None:
                    # if any port state is unknown, so is route
                    return None
                elif state:
                    if route == self.ROUTE_NONE:
                        route = port.port
                    else:
                        route = self.ROUTE_MULTIPLE
        return route

    def _set_route(self, route):
        '''Disconnect all ports and connect port specified by route.'''
        # reset all ports except route
        for port in self.parameters.values():
            if isinstance(port, SwitchPort):
                if (port.port != route) and (port.get() in (None, True)):
                    port.set(False)
        # set new route
        if route != self.ROUTE_NONE:
            port = self.parameters['{}_state'.format(route)]
            if not port.get():
                port.set(True)

    def direction(self, pin, direction):
        '''Set digital output `pin` direction to `direction` ('IN' or 'OUT')'''
        if direction not in ['IN', 'OUT']:
            raise ValueError('direction must be IN or OUT')
        self.write('SOUR:DIG:IO{:d} {}'.format(pin, direction))

    def outpin(self, pin, state):
        '''Set digital output `pin` to logical state `state`.'''
        self.write('SOUR:DIG:DATA{:d} {:d}'.format(pin, state))

    def pulse(self, pin, state, pulse_length=None):
        '''Pulse digital output `pin` to `state` for `pulse_length` seconds.'''
        if pulse_length is None:
            pulse_length = self.settle_time.get()
        self.write('SOUR:DIG:PULS{:d} {:d},{:f}'.format(pin, state, pulse_length))
        for port in self.parameters.values():
            if isinstance(port, SwitchPort):
                port.notify(pin)

class RadiallSwitch(VisaInstrument):
    PORTMAPS = {
        'sw0': dict(P1=(13,11), P2=(13,12), P3=(13,15), P4=(13,16)), # one cable tie
        'sw1': dict(P1=(26,21), P2=(26,22), P3=(26,23), P4=(26, 24)) # two cable ties
    }

    def __init__(self, name, address, portmaps={'sw0':'sw0', 'sw1':'sw1'}, 
                 settle_time=10e-3, reset=False, **kwargs):
        '''
        SQDLab room-temperatur switch driver

        The GPIO outputs of an RPi are directly connected to the TTL inputs of
        one or more latching Radiall switches.

        Arguments:
            name: `str`
                Name of the instrument
            address: `str`
                Visa resource id of the instrument
            portmaps: `dict` of `str`:`dict` pairs
                Channel name to portmap dict.
                Each portmap is either:
                * a port name to (reset pin, set pin) dict.
                * a string identifying a preset portmap (currently sw0, sw1)
        '''
        super().__init__(name, address, terminator='\n', **kwargs)

        for channel, portmap in portmaps.items():
            if isinstance(portmap, str):
                if portmap not in self.PORTMAPS:
                    raise KeyError('Unknown portmap {}.'.format(portmap))
                portmap = self.PORTMAPS[portmap]
            submodule = SwitchChannel(self, channel, portmap=portmap)
            self.add_submodule(channel, submodule)
            # optional reset
            submodule.settle_time.set(settle_time)
            if reset:
                submodule.route.set(SwitchChannel.ROUTE_NONE)

        self.connect_message()
