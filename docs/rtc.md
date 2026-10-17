# Board RTC selection

The clear database module family selects the clock through `DatabaseProfile.rtc_model`:

| Module family | Board | RTC adapter |
| --- | --- | --- |
| `616404xx` | M88/M90, 2 MB | Existing `Rtc4543` |
| `616406xx` | M202, 4 MB | `Rtc3049` |

The low module-version byte, file name, file length, decrypted runtime hash and
firmware routine addresses do not select the RTC. Address relocation still runs
independently. No new build whitelist, date restriction or launch guard is added.

M202 uses a command-based, MSB-first SPI calendar compatible with the
[RV-3049-C2 manual](https://www.microcrystal.com/fileadmin/Media/Products/RTC/App.Manual/RV-3049-C2_App-Manual.pdf),
sections 3.3 and 6.2. Reads start with bit 7 of the command set, writes with it
clear; the remaining seven bits specify the register. The register counter wraps
within its eight-register page. Transfers may read or write any number of bytes.
The first eight read bytes are retained for diagnostics; this does not limit
the transfer. `0x88`/`0x08` are ordinary accesses to the clock page, rather than
the only accepted commands.

The bridge reads GPIO direction at `FFF906` together with the port at `FFF907`.
CE, CLK and DATA use bits 6, 4 and 7. CPU output is preserved while direction
bit 7 is set; RTC input becomes visible when the firmware clears that bit.
Command/write bits are sampled on rising clock edges, and read data changes on
falling edges. The clock cache is stable for the entire CE transaction. Complete
written bytes commit when CE falls; an incomplete final byte is discarded.

The running clock supports seconds polling, partial calendar writes, leap days,
date rollover, 12/24-hour mode and a separately settable weekday counter. The WE
bit can stop and restart it. Invalid calendar writes remain visible for the
original firmware to reject. The guest's native clock validation is unchanged.
Registers outside the calendar retain written bytes; alarm/timer interrupts,
temperature conversion and EEPROM programming are not implemented here.

The existing `rtc4543.py` is unchanged. Its port-only GPIO behavior and log names
remain unchanged; M202 logs use `DB_RV3049_*`. Both models use the selected database
date and a monotonic running clock. They ship in the launcher runtime bundle.

Validation includes both RTC test suites, family selection across module-version
bytes, bridge/relocation/bundle regressions, and the isolated original M202 read,
write, seconds-poll and timestamp routines in
`analysis/m202-no-dump/probe_rtc4mb.py` (from the workspace root). That probe uses
the production adapter and requires the owner's runtime plus the local Unicorn
dependency. It is not evidence of a complete guest boot.
