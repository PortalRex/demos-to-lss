"""Generate a LiveSplit .lss splits file from a folder of Portal 2 speedrun demos.

Usage:
    Double-click the script (or "Demos to LSS.bat") for a folder picker,
    drag a demo folder onto the .bat, or from a terminal:
        python demos_to_lss.py <demo_folder> [--template T.lss] [--out O.lss]
    No template file is needed: the standard 62-split Portal 2 template is
    built in. A template.lss next to the script (or --template) overrides it,
    e.g. for custom split names. Output defaults to <folder name>.lss next
    to the demo folder.

How it works
------------
SAR (SourceAutoRecord) auto-records one demo per map session and, when the
speedrun timer auto-stops at the end of the run, writes its complete split
record into the final demo as a custom data message (type 0x0A): every split
with per-session tick counts, timer offset included. This script:

 1. Parses every demo's message stream (headers lie for SAR demos).
 2. Extracts SAR custom data: timestamps (0x0B), pause time (0x08) and the
    speedrun split record (0x0A).
 3. Rebuilds cumulative game times exactly the way SAR feeds LiveSplit:
    seconds = float32(ticks / 60), segment times = differences of those
    float32 cumulative values. The final split is reduced by one tick,
    matching LiveSplit's recorded end time (SAR's demo record counts the
    end-event tick inclusively; the live timer does not).
 4. Fills the template's segments with GameTime PB splits, best segment
    times and a single attempt-history entry.

Fallback: if no 0x0A record exists (run not finished / autostop off), per-map
tick sums are used instead (pause ticks are added only on sp_a1_wakeup, the
one map where SAR counts pause time). The first map uses sar_speedrun_offset
from the demos' config replay plus the fixed start-event tick; the run end
cannot be detected without the record, so a warning is printed.
"""
import argparse
import glob
import os
import re
import struct
import sys
from datetime import datetime, timedelta

TICKRATE = 60
MSSC = 2  # Portal 2 max splitscreen slots (democmdinfo is 76 bytes per slot)
HEADER_SIZE = 1072

# Legacy-mode constants (used when demos predate SAR's embedded speedrun
# record). Calibrated against SAR-recorded runs and UntitledParser:
DEFAULT_OFFSET = 18980        # standard SP timer offset, 5:16.33
AUTOSAVE_AFTER_START = 215    # intro1's scripted autosave fires 215 ticks
                              # after the timer-start moment
MOONSHOT_AFTER_ANCHOR = 6     # portal opens on the moon 6 ticks after the
                              # ending script's map_wants_save_disable

# Portal 2 singleplayer maps in order -> used to sanity-check demo ordering
P2_MAPS = [
    'sp_a1_intro1', 'sp_a1_intro2', 'sp_a1_intro3', 'sp_a1_intro4',
    'sp_a1_intro5', 'sp_a1_intro6', 'sp_a1_intro7', 'sp_a1_wakeup',
    'sp_a2_intro',
    'sp_a2_laser_intro', 'sp_a2_laser_stairs', 'sp_a2_dual_lasers',
    'sp_a2_laser_over_goo', 'sp_a2_catapult_intro', 'sp_a2_trust_fling',
    'sp_a2_pit_flings', 'sp_a2_fizzler_intro',
    'sp_a2_sphere_peek', 'sp_a2_ricochet', 'sp_a2_bridge_intro',
    'sp_a2_bridge_the_gap', 'sp_a2_turret_intro', 'sp_a2_laser_relays',
    'sp_a2_turret_blocker', 'sp_a2_laser_vs_turret', 'sp_a2_pull_the_rug',
    'sp_a2_column_blocker', 'sp_a2_laser_chaining', 'sp_a2_triple_laser',
    'sp_a2_bts1', 'sp_a2_bts2', 'sp_a2_bts3', 'sp_a2_bts4', 'sp_a2_bts5',
    'sp_a2_bts6', 'sp_a2_core',
    'sp_a3_00', 'sp_a3_01', 'sp_a3_03', 'sp_a3_jump_intro',
    'sp_a3_bomb_flings', 'sp_a3_crazy_box', 'sp_a3_transition01',
    'sp_a3_speed_ramp', 'sp_a3_speed_flings', 'sp_a3_portal_intro',
    'sp_a3_end',
    'sp_a4_intro', 'sp_a4_tb_intro', 'sp_a4_tb_trust_drop',
    'sp_a4_tb_wall_button', 'sp_a4_tb_polarity', 'sp_a4_tb_catch',
    'sp_a4_stop_the_box', 'sp_a4_laser_catapult', 'sp_a4_laser_platform',
    'sp_a4_speed_tb_catch', 'sp_a4_jump_polarity',
    'sp_a4_finale1', 'sp_a4_finale2', 'sp_a4_finale3', 'sp_a4_finale4',
]


# Default split names, index-aligned with P2_MAPS. Used to build the
# built-in template when no template.lss is present.
SEGMENT_NAMES = [
    '-Container Ride', '-Portal Carousel', '-Portal Gun', '-Smooth Jazz',
    '-Cube Momentum', '-Future Starter', '-Secret Panel', '-Wakeup',
    '{Chapter 1} Incinerator',
    '-Laser Intro', '-Laser Stairs', '-Dual Lasers', '-Laser Over Goo',
    '-Catapult Intro', '-Trust Fling', '-Pit Flings',
    '{Chapter 2} Fizzler Intro',
    '-Ceiling Catapult', '-Ricochet', '-Bridge Intro', '-Bridge the Gap',
    '-Turret Intro', '-Laser Relays', '-Turret Blocker', '-Laser vs. Turret',
    '{Chapter 3} Pull the Rug',
    '-Column Blocker', '-Laser Chaining', '-Triple Laser', '-Jailbreak',
    '{Chapter 4} Escape',
    '-Turret Factory', '-Turret Sabotage', '-Neurotoxin Sabotage',
    '-Tube Ride', '{Chapter 5} Core',
    '-Long Fall', '-Underground', '-Cave Johnson', '-Repulsion Intro',
    '-Bomb Flings', '-Crazy Box', '{Chapter 6} PotatOS',
    '-Prop Intro', '-Prop Flings', '-Conversion Intro',
    '{Chapter 7} Three Gels',
    '-Test', '-Funnel Intro', '-Ceiling Button', '-Wall Button', '-Polarity',
    '-Funnel Catch', '-Stop the Box', '-Laser Catapult', '-Laser Platform',
    '-Prop Catch', '{Chapter 8} Repulsion Polarity',
    '-Finale 1', '-Finale 2', '-Finale 3', '{Chapter 9} Finale 4',
]


def builtin_template():
    segs = '\n'.join(
        '    <Segment>\n'
        f'      <Name>{name}</Name>\n'
        '      <Icon />\n'
        '      <SplitTimes>\n'
        '        <SplitTime name="Personal Best" />\n'
        '      </SplitTimes>\n'
        '      <BestSegmentTime />\n'
        '      <SegmentHistory />\n'
        '    </Segment>' for name in SEGMENT_NAMES)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Run version="1.7.0">\n'
        '  <GameIcon />\n'
        '  <GameName>Portal 2</GameName>\n'
        '  <CategoryName>Single Player</CategoryName>\n'
        '  <LayoutPath>\n'
        '  </LayoutPath>\n'
        '  <Metadata>\n'
        '    <Run id="" />\n'
        '    <Platform usesEmulator="False">PC</Platform>\n'
        '    <Region>\n'
        '    </Region>\n'
        '    <Variables>\n'
        '      <Variable name="Quicksaves">Yes</Variable>\n'
        '      <Variable name="Singleplayer Category">No SLA</Variable>\n'
        '    </Variables>\n'
        '  </Metadata>\n'
        '  <Offset>00:05:16.3300000</Offset>\n'
        '  <AttemptCount>0</AttemptCount>\n'
        '  <AttemptHistory />\n'
        '  <Segments>\n'
        f'{segs}\n'
        '  </Segments>\n'
        '  <AutoSplitterSettings />\n'
        '</Run>')


class Demo:
    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)
        self.map = None
        self.max_tick = 0          # last packet tick (session length in ticks)
        self.pause_ticks = 0       # SAR 0x08 records (paused ticks, not in demo time)
        self.timestamp = None      # SAR 0x0B record (wall clock at demo start)
        self.split_record = None   # SAR 0x0A record: [(split, [(seg, ticks)])]
        self.split_record_tick = None
        self.cvars = {}            # last sar_speedrun_* values replayed in this demo
        self.autosave_tick = None  # first 'autosave' console cmd (tick > 0)
        self.end_anchor_tick = None  # first 'map_wants_save_disable' cmd (tick > 0)


def parse_demo(path):
    d = Demo(path)
    with open(path, 'rb') as f:
        data = f.read()
    if data[:8] != b'HL2DEMO\x00':
        raise ValueError(f'{path}: not a Source demo')
    d.map = data[536:796].split(b'\0')[0].decode(errors='replace')
    pos, n = HEADER_SIZE, len(data)
    while pos < n:
        cmd = data[pos]; pos += 1
        if cmd == 7:  # dem_stop
            break
        tick, = struct.unpack_from('<i', data, pos)
        pos += 5  # tick + player-slot byte (demo protocol 4)
        if cmd in (1, 2):  # signon / packet
            pos += 76 * MSSC + 8
            size, = struct.unpack_from('<i', data, pos)
            pos += 4 + size
            if cmd == 2 and tick > d.max_tick:
                d.max_tick = tick
        elif cmd == 3:  # synctick
            pass
        elif cmd == 4:  # consolecmd
            size, = struct.unpack_from('<i', data, pos)
            pos += 4
            cmdstr = data[pos:pos + size].split(b'\0')[0].decode(errors='replace')
            pos += size
            m = re.match(r'(sar_speedrun_(?:offset|time_pauses)) (\d+)', cmdstr)
            if m:
                d.cvars[m.group(1)] = int(m.group(2))
            elif tick > 0:
                if d.autosave_tick is None and cmdstr.strip() == 'autosave':
                    d.autosave_tick = tick
                elif (d.end_anchor_tick is None
                        and cmdstr.startswith('map_wants_save_disable')):
                    d.end_anchor_tick = tick
        elif cmd in (6, 9):  # datatables / stringtables
            size, = struct.unpack_from('<i', data, pos)
            pos += 4 + size
        elif cmd == 5:  # usercmd
            pos += 4
            size, = struct.unpack_from('<i', data, pos)
            pos += 4 + size
        elif cmd == 8:  # customdata
            typ, = struct.unpack_from('<i', data, pos); pos += 4
            size, = struct.unpack_from('<i', data, pos); pos += 4
            payload = data[pos:pos + size]; pos += size
            if typ == 0 and len(payload) >= 9:
                sub = payload[8]
                body = payload[9:]
                if sub == 0x08:    # pause time in ticks
                    d.pause_ticks += struct.unpack_from('<I', body)[0]
                elif sub == 0x0B:  # timestamp: u16 year, month(0-based), d, h, m, s
                    y, = struct.unpack_from('<H', body)
                    mo, day, h, mi, s = body[2:7]
                    d.timestamp = datetime(y, mo + 1, day, h, mi, s)
                elif sub == 0x0A:  # speedrun split record
                    d.split_record = parse_split_record(body)
                    d.split_record_tick = tick
        else:
            raise ValueError(f'{path}: unknown demo message {cmd} @ {pos - 6}')
    return d


def parse_split_record(body):
    off = 0
    nsplits, = struct.unpack_from('<I', body, off); off += 4
    splits = []
    for _ in range(nsplits):
        end = body.index(b'\0', off)
        sname = body[off:end].decode(); off = end + 1
        nsegs, = struct.unpack_from('<I', body, off); off += 4
        segs = []
        for _ in range(nsegs):
            end = body.index(b'\0', off)
            gname = body[off:end].decode(); off = end + 1
            ticks, = struct.unpack_from('<I', body, off); off += 4
            segs.append((gname, ticks))
        splits.append((sname, segs))
    return splits


def demo_sort_key(path):
    stem = os.path.basename(path)[:-4]
    m = re.search(r'_(\d+)$', stem)
    return int(m.group(1)) if m else 1


def f32(x):
    return struct.unpack('<f', struct.pack('<f', x))[0]


def to_ticks100ns(seconds):
    return int(seconds * 10_000_000)  # truncate, matches LiveSplit


def fmt_timespan(ticks100ns):
    """Format like C# TimeSpan.ToString(): HH:MM:SS[.fffffff]."""
    frac = ticks100ns % 10_000_000
    total_s = ticks100ns // 10_000_000
    h, rem = divmod(total_s, 3600)
    m, s = divmod(rem, 60)
    base = f'{h:02d}:{m:02d}:{s:02d}'
    return base if frac == 0 else f'{base}.{frac:07d}'


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def build_lss(demo_folder, template_path, out_path, log=print):
    files = sorted(glob.glob(os.path.join(demo_folder, '*.dem')),
                   key=demo_sort_key)
    if not files:
        raise RuntimeError(f'No .dem files found in {demo_folder}')
    if template_path is not None and not os.path.isfile(template_path):
        raise RuntimeError(f'Template not found: {template_path}')

    # keep only the dominant demo series; stray files (e.g. someone else's
    # demo dropped into the folder) don't share the series' filename prefix
    groups = {}
    for f in files:
        prefix = re.sub(r'_\d+$', '', os.path.basename(f)[:-4])
        groups.setdefault(prefix, []).append(f)
    if len(groups) > 1:
        keep = max(groups, key=lambda k: len(groups[k]))
        for k, v in groups.items():
            if k != keep:
                log(f'ignoring {len(v)} demo(s) not in the "{keep}" series: '
                    + ', '.join(os.path.basename(x) for x in v))
        files = sorted(groups[keep], key=demo_sort_key)

    demos = [parse_demo(f) for f in files]
    log(f'parsed {len(demos)} demos '
        f'({demos[0].map} ... {demos[-1].map})')

    record_demo = next((d for d in reversed(demos) if d.split_record), None)
    if record_demo is not None:
        log(f'using SAR speedrun record from {record_demo.name} '
            f'(written at tick {record_demo.split_record_tick})')
        seg_ticks = [sum(t for _, t in segs)
                     for _, segs in record_demo.split_record]
        # SAR's demo record counts the run-end tick inclusively; the live
        # timer LiveSplit records stops one tick earlier.
        seg_ticks[-1] -= 1
    else:
        log('WARNING: no SAR speedrun record found in any demo; '
            'reconstructing from per-map tick sums (legacy mode)')
        # cvar values replayed into each demo's cfg; last one seen wins
        offset = None
        for d in demos:
            offset = d.cvars.get('sar_speedrun_offset', offset)
        if offset is None:
            offset = DEFAULT_OFFSET
            log(f'  no sar_speedrun_offset in demos; assuming the standard '
                f'{DEFAULT_OFFSET} ticks (5:16.33)')
        # run start: intro1's scripted autosave fires a fixed 215 ticks
        # after the timer-start moment, independent of recorder version
        first = demos[0]
        if first.autosave_tick is not None:
            start_tick = first.autosave_tick - AUTOSAVE_AFTER_START
        else:
            start_tick = 343
            log('  no autosave in first demo; assuming timer start at '
                'demo tick 343')
        # run end: the ending script's map_wants_save_disable fires 6 ticks
        # before the portal opens on the moon (matches UntitledParser)
        last = demos[-1]
        if last.map == 'sp_a4_finale4' and last.end_anchor_tick is not None:
            end_tick = last.end_anchor_tick + MOONSHOT_AFTER_ANCHOR
            log(f'  run end anchored at tick {end_tick} of {last.name}; '
                f'{last.max_tick - end_tick} trailing ticks discarded')
        else:
            end_tick = last.max_tick
            log('  WARNING: no ending anchor in the last demo; the final '
                'split includes everything recorded after the moon shot')
        seg_ticks = []
        cur_map, cur = None, 0
        for i, d in enumerate(demos):
            if d.map != cur_map and cur_map is not None:
                seg_ticks.append(cur)
                cur = 0
            cur_map = d.map
            cur += end_tick if d is last else d.max_tick
            if d.map == 'sp_a1_wakeup':
                # SAR counts pause time only on sp_a1_wakeup (anti
                # pause-abuse rule); pauses anywhere else don't add time
                cur += d.pause_ticks
            if i == 0:
                cur += offset - start_tick
        seg_ticks.append(cur)

    # Cumulative game times exactly as SAR reports them to LiveSplit:
    # float32(total_ticks * float32(1/60)) -- SAR multiplies by the float32
    # constant 1/60, it does not divide. Segment times are differences.
    sixtieth = f32(1.0 / TICKRATE)
    cums, total = [], 0
    for t in seg_ticks:
        total += t
        cums.append(to_ticks100ns(f32(total * sixtieth)))
    # Segment times are differences of the stored (truncated) TimeSpans,
    # exactly as LiveSplit computes segment history entries.
    seg_times = [cums[0]] + [cums[i] - cums[i - 1] for i in range(1, len(cums))]

    # Attempt wall-clock times (approximate: demo timestamps are written at
    # recording start; menu pause before the run start is added).
    started = demos[0].timestamp
    if started and demos[0].pause_ticks:
        started += timedelta(seconds=round(demos[0].pause_ticks / TICKRATE))
    ended = demos[-1].timestamp
    if ended:
        end_tick = (record_demo.split_record_tick
                    if record_demo is demos[-1] else demos[-1].max_tick)
        ended += timedelta(seconds=round(end_tick / TICKRATE))

    if template_path is not None:
        log(f'using template {template_path}')
        with open(template_path, encoding='utf-8-sig') as f:
            template = f.read()
    else:
        template = builtin_template()

    seg_names = re.findall(r'<Name>(.*?)</Name>', template, re.S)
    if len(seg_names) != len(seg_ticks):
        raise RuntimeError(
            f'Template has {len(seg_names)} segments but the demos produced '
            f'{len(seg_ticks)} splits — the demo set is probably incomplete '
            f'(missing maps or missing save/load sessions)')

    out = template
    out = re.sub(r'<AttemptCount>\d+</AttemptCount>',
                 '<AttemptCount>1</AttemptCount>', out)
    ts = '{:%m/%d/%Y %H:%M:%S}'
    attempt = (
        f'<AttemptHistory>\n'
        f'    <Attempt id="1"'
        + (f' started="{ts.format(started)}" isStartedSynced="True"' if started else '')
        + (f' ended="{ts.format(ended)}" isEndedSynced="True"' if ended else '')
        + f'>\n      <GameTime>{fmt_timespan(cums[-1])}</GameTime>\n'
        f'    </Attempt>\n  </AttemptHistory>'
    )
    out = out.replace('<AttemptHistory />', attempt)

    pieces = out.split('<Segment>')
    assert len(pieces) == len(seg_ticks) + 1
    rebuilt = [pieces[0]]
    for i, piece in enumerate(pieces[1:]):
        cum_s = fmt_timespan(cums[i])
        seg_s = fmt_timespan(seg_times[i])
        piece = piece.replace(
            '<SplitTime name="Personal Best" />',
            f'<SplitTime name="Personal Best">\n'
            f'          <GameTime>{cum_s}</GameTime>\n'
            f'        </SplitTime>')
        piece = piece.replace(
            '<BestSegmentTime />',
            f'<BestSegmentTime>\n'
            f'        <GameTime>{seg_s}</GameTime>\n'
            f'      </BestSegmentTime>')
        piece = piece.replace(
            '<SegmentHistory />',
            f'<SegmentHistory>\n'
            f'        <Time id="1">\n'
            f'          <GameTime>{seg_s}</GameTime>\n'
            f'        </Time>\n'
            f'      </SegmentHistory>')
        rebuilt.append(piece)
    out = '<Segment>'.join(rebuilt)

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(out)
    log(f'wrote {out_path}: {len(seg_ticks)} splits, '
        f'final time {fmt_timespan(cums[-1])}')
    return out_path


def default_paths(demo_folder, template=None, out=None):
    folder = os.path.normpath(os.path.abspath(demo_folder))
    if template is None:
        # a template.lss next to the script overrides the built-in template
        candidate = os.path.join(SCRIPT_DIR, 'template.lss')
        template = candidate if os.path.isfile(candidate) else None
    if out is None:
        out = os.path.join(os.path.dirname(folder),
                           os.path.basename(folder) + '.lss')
    return template, out


def cli():
    ap = argparse.ArgumentParser(
        description='Generate a LiveSplit .lss from Portal 2 SAR demos')
    ap.add_argument('demo_folder')
    ap.add_argument('--template', default=None,
                    help='splits template overriding the built-in one '
                         '(default: template.lss next to this script, '
                         'if present)')
    ap.add_argument('--out', default=None,
                    help='output file (default: <folder name>.lss next to '
                         'the demo folder)')
    args = ap.parse_args()
    template, out = default_paths(args.demo_folder, args.template, args.out)
    try:
        build_lss(args.demo_folder, template, out)
    except RuntimeError as e:
        sys.exit(f'error: {e}')


def gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox

    root = tk.Tk()
    root.withdraw()
    folder = filedialog.askdirectory(
        title="Select the folder containing the run's demos (.dem files)")
    if not folder:
        return
    template, out_default = default_paths(folder)
    out = filedialog.asksaveasfilename(
        title='Save splits as',
        defaultextension='.lss',
        initialdir=os.path.dirname(out_default),
        initialfile=os.path.basename(out_default),
        filetypes=[('LiveSplit splits', '*.lss'), ('All files', '*.*')])
    if not out:
        return
    lines = []
    try:
        build_lss(folder, template, out,
                  log=lambda msg: lines.append(str(msg)))
    except Exception as e:
        messagebox.showerror('Demos to LSS', f'Failed:\n\n{e}')
        return
    messagebox.showinfo('Demos to LSS', 'Done!\n\n' + '\n'.join(lines))


if __name__ == '__main__':
    if len(sys.argv) > 1:
        cli()
    else:
        gui()
