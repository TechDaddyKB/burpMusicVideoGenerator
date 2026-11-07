#!/usr/bin/env python3
"""Desktop app for burp_song.py: pick a streamer, dates, confidence and output, then go.

Run `python burp_gui.py`.
"""
from __future__ import annotations

import base64
import contextlib
from datetime import date
import io
import logging
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk
import urllib.request

import burp_song as bs

HERE = Path(__file__).resolve().parent
WORK_DIR = HERE/'burp-work'
OUTPUT_DIR = HERE/'outputs'


def build_argv(username, from_date, to_date, confidence, midi, output, download_all=False, cpu_only=False):
    """Check the form and turn it into burp_song.py arguments. Raises ValueError with a friendly message."""
    username = username.strip().lower()
    if not username:
        raise ValueError('Search for a streamer and select one, or type their username.')
    dates = {}
    for label, value in (('From', from_date), ('To', to_date)):
        value = value.strip()
        if value:
            try:
                dates[label] = date.fromisoformat(value)
            except ValueError:
                raise ValueError(f'{label} date must look like 2026-10-31.') from None
    if len(dates) == 2 and dates['From'] > dates['To']:
        raise ValueError('The From date must be on or before the To date.')
    if not midi or not Path(midi).is_file():
        raise ValueError('Choose a MIDI file.')
    if not output.strip():
        raise ValueError('Choose where to save the video.')
    output = Path(output.strip())
    if output.suffix.lower() != '.mp4':
        output = output.with_name(output.name+'.mp4')
    if output.exists():
        raise ValueError(f'{output.name} already exists. Choose another name.')
    argv = [username, '--midi', str(midi), '--output', str(output), '--work-dir', str(WORK_DIR),
            '--confidence', f'{confidence:.2f}']
    if 'From' in dates:
        argv += ['--from-date', dates['From'].isoformat()]
    if 'To' in dates:
        argv += ['--to-date', dates['To'].isoformat()]
    if download_all and not dates:
        argv.append('--all')
    if cpu_only:
        argv.append('--cpu-only')
    return argv, output


def open_path(path):
    if sys.platform.startswith('win'):
        os.startfile(path)  # noqa: S606 - opens the user's own file
    else:
        subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(path)])


class QueueLog(logging.Handler):
    def __init__(self, target):
        super().__init__()
        self.target = target

    def emit(self, record):
        self.target.put(('log', self.format(record)))


class App:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.images = {}
        self.running = False
        self.output = None
        root.title('Burp Music Video Generator')
        root.minsize(620, 720)
        root.geometry('720x860')
        style = ttk.Style(root)
        style.configure('Treeview', rowheight=40)
        title_font = tkfont.nametofont('TkDefaultFont').copy()
        title_font.configure(size=16, weight='bold')
        style.configure('Title.TLabel', font=title_font)
        style.configure('Hint.TLabel', foreground='gray45')

        frame = ttk.Frame(root, padding=16)
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text='Burp Music Video Generator', style='Title.TLabel').grid(sticky='w')
        ttk.Label(frame, text='From indigestion to orchestration.', style='Hint.TLabel').grid(sticky='w', pady=(0, 10))

        # Streamer
        box = ttk.LabelFrame(frame, text='Streamer', padding=10)
        box.grid(sticky='nsew', pady=4)
        frame.rowconfigure(2, weight=1)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(1, weight=1)
        self.query = tk.StringVar(value='emmytheaxolotl')
        entry = ttk.Entry(box, textvariable=self.query)
        entry.grid(row=0, column=0, sticky='ew')
        entry.bind('<Return>', lambda e: self.search())
        self.search_button = ttk.Button(box, text='Search', command=self.search)
        self.search_button.grid(row=0, column=1, padx=(6, 0))
        self.results = ttk.Treeview(box, columns=('followers', 'live'), height=5, selectmode='browse')
        self.results.heading('#0', text='Channel')
        self.results.heading('followers', text='Followers')
        self.results.heading('live', text='')
        self.results.column('#0', width=300)
        self.results.column('followers', width=100, anchor='e')
        self.results.column('live', width=60, anchor='center')
        self.results.grid(row=1, column=0, columnspan=2, sticky='nsew', pady=(8, 0))
        self.results.bind('<<TreeviewSelect>>', self.pick_streamer)
        self.streamer = tk.StringVar()
        self.streamer_label = ttk.Label(box, text='No streamer selected. Search, then click one.', style='Hint.TLabel')
        self.streamer_label.grid(row=2, column=0, columnspan=2, sticky='w', pady=(6, 0))

        # Broadcasts
        box = ttk.LabelFrame(frame, text='Broadcasts (optional)', padding=10)
        box.grid(sticky='ew', pady=4)
        self.from_date, self.to_date = tk.StringVar(), tk.StringVar()
        ttk.Label(box, text='From').grid(row=0, column=0, sticky='w')
        ttk.Entry(box, textvariable=self.from_date, width=12).grid(row=0, column=1, padx=(6, 16))
        ttk.Label(box, text='To').grid(row=0, column=2, sticky='w')
        ttk.Entry(box, textvariable=self.to_date, width=12).grid(row=0, column=3, padx=(6, 0))
        ttk.Label(box, text='YYYY-MM-DD, by broadcast start date (UTC). Leave both empty to reuse your '
                  'clips, or download every broadcast if you have none yet.', style='Hint.TLabel',
                  wraplength=560).grid(row=1, column=0, columnspan=4, sticky='w', pady=(6, 0))
        self.download_all = tk.BooleanVar()
        self.all_check = ttk.Checkbutton(box, text='Download every broadcast even if I already have clips',
                                         variable=self.download_all)
        self.all_check.grid(row=2, column=0, columnspan=4, sticky='w', pady=(4, 0))
        for var in (self.from_date, self.to_date):
            var.trace_add('write', lambda *a: self.update_all_check())

        # Detection
        box = ttk.LabelFrame(frame, text='Burp detection confidence', padding=10)
        box.grid(sticky='ew', pady=4)
        box.columnconfigure(1, weight=1)
        self.confidence = tk.DoubleVar(value=.35)
        self.confidence_label = ttk.Label(box, width=5)
        ttk.Label(box, text='More burps', style='Hint.TLabel').grid(row=0, column=0)
        ttk.Scale(box, from_=.05, to=.95, variable=self.confidence,
                  command=lambda v: self.show_confidence()).grid(row=0, column=1, sticky='ew', padx=8)
        ttk.Label(box, text='Surer burps', style='Hint.TLabel').grid(row=0, column=2)
        self.confidence_label.grid(row=0, column=3, padx=(8, 0))
        self.show_confidence()

        # Song and output
        box = ttk.LabelFrame(frame, text='Song', padding=10)
        box.grid(sticky='ew', pady=4)
        box.columnconfigure(1, weight=1)
        self.midi, self.output_path = tk.StringVar(), tk.StringVar(value=str(OUTPUT_DIR/'burp-song.mp4'))
        ttk.Label(box, text='MIDI file').grid(row=0, column=0, sticky='w')
        ttk.Entry(box, textvariable=self.midi).grid(row=0, column=1, sticky='ew', padx=6)
        ttk.Button(box, text='Browse…', command=self.choose_midi).grid(row=0, column=2)
        ttk.Label(box, text='Save video as').grid(row=1, column=0, sticky='w', pady=(6, 0))
        ttk.Entry(box, textvariable=self.output_path).grid(row=1, column=1, sticky='ew', padx=6, pady=(6, 0))
        ttk.Button(box, text='Choose…', command=self.choose_output).grid(row=1, column=2, pady=(6, 0))

        # Encoder, start button, progress
        row = ttk.Frame(frame)
        row.grid(sticky='ew', pady=(8, 4))
        row.columnconfigure(0, weight=1)
        self.encoder_label = ttk.Label(row, text='Video encoding: checking for a GPU…', style='Hint.TLabel')
        self.encoder_label.grid(row=0, column=0, sticky='w')
        self.cpu_only = tk.BooleanVar()
        ttk.Checkbutton(row, text='Use CPU only', variable=self.cpu_only).grid(row=0, column=1)
        self.start_button = ttk.Button(frame, text='Make the video', command=self.start)
        self.start_button.grid(sticky='ew', ipady=6, pady=4)
        self.progress = ttk.Progressbar(frame, mode='indeterminate')
        self.progress.grid(sticky='ew', pady=4)
        self.status = ttk.Label(frame, text='Ready.')
        self.status.grid(sticky='w')
        self.log = tk.Text(frame, height=8, wrap='word', state='disabled', font=('TkFixedFont', 9))
        self.log.grid(sticky='nsew', pady=4)
        frame.rowconfigure(self.log.grid_info()['row'], weight=1)
        done = ttk.Frame(frame)
        done.grid(sticky='ew')
        self.open_button = ttk.Button(done, text='Play video', command=lambda: open_path(self.output), state='disabled')
        self.open_button.pack(side='left')
        self.folder_button = ttk.Button(done, text='Show folder', state='disabled',
                                        command=lambda: open_path(self.output.parent))
        self.folder_button.pack(side='left', padx=6)

        root.protocol('WM_DELETE_WINDOW', self.close)
        self.background(lambda: self.events.put(('encoder', bs.pick_encoder())))
        root.after(100, self.poll)

    # --- helpers --------------------------------------------------------------

    def background(self, work):
        threading.Thread(target=work, daemon=True).start()

    def write_log(self, line):
        self.log.configure(state='normal')
        self.log.insert('end', line+'\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    def show_confidence(self):
        self.confidence.set(round(self.confidence.get()/.05)*.05)
        self.confidence_label.configure(text=f'{self.confidence.get():.2f}')

    def update_all_check(self):
        has_dates = self.from_date.get().strip() or self.to_date.get().strip()
        self.all_check.state(['disabled'] if has_dates else ['!disabled'])

    # --- streamer search ------------------------------------------------------

    def search(self):
        query = self.query.get().strip()
        if not query:
            return
        self.search_button.state(['disabled'])
        self.status.configure(text=f'Searching Twitch for “{query}”…')

        def work():
            try:
                channels = bs.search_channels(query)
                self.events.put(('channels', (query, channels)))
                for channel in channels:
                    if channel['image']:
                        with urllib.request.urlopen(channel['image'], timeout=10) as r:
                            self.events.put(('avatar', (channel['login'], r.read())))
            except Exception as exc:
                self.events.put(('search_error', str(exc)))
        self.background(work)

    def show_channels(self, query, channels):
        self.search_button.state(['!disabled'])
        self.results.delete(*self.results.get_children())
        for c in channels:
            followers = f"{c['followers']:,}" if c['followers'] is not None else ''
            self.results.insert('', 'end', iid=c['login'], text=f"  {c['name']}  ({c['login']})",
                                values=(followers, 'LIVE' if c['live'] else ''))
        exact = query.lower()
        if not channels:
            # Search found nothing; the typed name may still be a real username.
            self.results.insert('', 'end', iid=exact, text=f'  Use “{exact}” as typed', values=('', ''))
        self.status.configure(text=f'{len(channels)} channels found.' if channels else 'No channels found.')
        if exact in self.results.get_children():
            self.results.selection_set(exact)

    def show_avatar(self, login, data):
        try:
            image = tk.PhotoImage(data=base64.b64encode(data))
            if image.width() > 40:
                image = image.subsample(max(1, round(image.width()/36)))
            self.images[login] = image
            if self.results.exists(login):
                self.results.item(login, image=image)
        except tk.TclError:
            pass  # a format Tk can't read; keep the row without a picture

    def pick_streamer(self, event=None):
        selection = self.results.selection()
        if selection:
            self.streamer.set(selection[0])
            self.streamer_label.configure(text=f'Selected: {selection[0]}', style='TLabel')

    # --- files ----------------------------------------------------------------

    def choose_midi(self):
        path = filedialog.askopenfilename(title='Choose a MIDI file',
                                          filetypes=[('MIDI files', '*.mid *.midi'), ('All files', '*')])
        if path:
            self.midi.set(path)

    def choose_output(self):
        OUTPUT_DIR.mkdir(exist_ok=True)
        current = Path(self.output_path.get() or OUTPUT_DIR/'burp-song.mp4')
        path = filedialog.asksaveasfilename(title='Save the video as', defaultextension='.mp4',
                                            initialdir=current.parent, initialfile=current.name,
                                            filetypes=[('MP4 video', '*.mp4')])
        if path:
            self.output_path.set(path)

    # --- making the video -----------------------------------------------------

    def start(self):
        try:
            argv, output = build_argv(self.streamer.get(), self.from_date.get(), self.to_date.get(),
                                      self.confidence.get(), self.midi.get(), self.output_path.get(),
                                      self.download_all.get(), self.cpu_only.get())
        except ValueError as exc:
            messagebox.showwarning('Almost there', str(exc), parent=self.root)
            return
        output.parent.mkdir(parents=True, exist_ok=True)
        self.running, self.output = True, output
        self.start_button.state(['disabled'])
        self.open_button.state(['disabled'])
        self.folder_button.state(['disabled'])
        self.progress.start(12)
        self.status.configure(text='Working… downloading and scanning can take a while for long broadcasts.')
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')

        def work():
            errors = io.StringIO()
            try:
                with contextlib.redirect_stderr(errors):
                    bs.main(argv)
                self.events.put(('done', None))
            except SystemExit as exc:
                lines = errors.getvalue().strip().splitlines()
                self.events.put(('failed', lines[-1].split('error: ', 1)[-1] if lines else str(exc)))
            except Exception as exc:
                self.events.put(('failed', str(exc)))
        self.background(work)

    def finish(self, error=None):
        self.running = False
        self.progress.stop()
        self.start_button.state(['!disabled'])
        if error:
            self.status.configure(text='Something went wrong. See the log below.')
            self.write_log(f'Error: {error}')
            messagebox.showerror('The video was not made', error, parent=self.root)
        else:
            self.status.configure(text=f'Done! Saved {self.output}')
            self.open_button.state(['!disabled'])
            self.folder_button.state(['!disabled'])

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'log':
                    self.write_log(value)
                elif kind == 'encoder':
                    gpu = value['id'] != 'cpu'
                    self.encoder_label.configure(
                        text=f"Video encoding: {value['name']}" + (' (GPU)' if gpu else ' — no GPU encoder found'))
                elif kind == 'channels':
                    self.show_channels(*value)
                elif kind == 'avatar':
                    self.show_avatar(*value)
                elif kind == 'search_error':
                    self.search_button.state(['!disabled'])
                    self.status.configure(text='Twitch search failed. You can still type a username and press Search.')
                    self.write_log(f'Search error: {value}')
                elif kind == 'done':
                    self.finish()
                elif kind == 'failed':
                    self.finish(value)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def close(self):
        if self.running and not messagebox.askyesno(
                'Stop making the video?',
                'A video is still being made. Quit anyway? Finished clips are kept, and the same '
                'settings will pick up where it stopped.', parent=self.root):
            return
        self.root.destroy()


def main():
    root = tk.Tk()
    app = App(root)
    handler = QueueLog(app.events)
    handler.setFormatter(logging.Formatter('%(asctime)s  %(message)s', '%H:%M:%S'))
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    logging.getLogger().addHandler(handler)
    root.mainloop()


if __name__ == '__main__':
    main()
