# -*- coding: utf-8 -*-
"""
Created on Sun Jun 25 22:58:30 2017

@author: Winand

https://pythonspot.com/en/create-a-chrome-plugin-with-python/
"""

from pathlib import Path
import shutil
import threading
import win32file
import win32con
import winnt
import time
from pyscript import script2js, parser0
import sys
import os
import re
from bs4 import BeautifulSoup, Comment
import fnmatch
import glob
import json
import argparse

args = argparse.ArgumentParser()
args.add_argument('path')
args.add_argument('--monitor', action='store_true')
args = args.parse_args(sys.argv[1:])
dir_ext = Path(args.path).resolve()

manifest_file = "manifest.py"
output_dir = dir_ext/"build"
file_names = set()

patt_css_url = re.compile(r'url\(["\']?(?P<url>[^"\']+)["\']?\)')


def print_err(*e, newline=True):
    if newline:
        print()
    print(*e, file=sys.stderr, flush=True)
    time.sleep(0.1)


def compile_py(src, dest):
    try:
        script2js(str(src), target=str(dest))
    except (FileNotFoundError, SyntaxError, parser0.JSError) as e:
        print_err(e)
        return False
    return True


def compile_html(src, dest):
    with open(src, 'rb') as f:
        html = BeautifulSoup(f.read().decode(), "lxml")
    # Remove comments
    [i.extract() for i in html.findAll(text=lambda _: isinstance(_, Comment))]
    files = [i['src'] for i in html.find_all("script")]
    files += [i['href'] for i in html.find_all("link")]
    # FIXME: parse <style>
    for file_name in files:
        matched = False
        for i in file_names:
            if fnmatch.fnmatch(file_name, i):
                matched = True
                break
        if not matched:
            print_err("#", file_name)

    try:
        with open(dest, "wb") as file:
            file.write(html.prettify("utf-8"))
        return True
    except FileNotFoundError as e:
        print_err(e)


def parse_css_urls(src):
    with open(src, 'rb') as f:
        css = f.read().decode()
    css = re.sub(r'\/\*.*?\*\/', '', css)
    files = patt_css_url.findall(css)
    for file_name in files:
        matched = False
        file_name = str(Path(file_name))  # collapse single dot
        for i in file_names:
            if fnmatch.fnmatch(file_name, i):
                matched = True
                break
        if not matched:
            print_err("#", file_name)


class Monitor():
    actions = {1: "add", 2: "del", 3: "update",
               4: "renamed_from", 5: "renamed_to"}
    FILE_NOTIFY_CHANGE = (win32con.FILE_NOTIFY_CHANGE_FILE_NAME |
                          win32con.FILE_NOTIFY_CHANGE_DIR_NAME |
                          win32con.FILE_NOTIFY_CHANGE_LAST_WRITE)

    def __init__(self, path, callback, dir_updates=False, forever=False):
        self.watch, self.loader_lock = {}, threading.RLock()
        self.path, self.callback = path, callback
        self.dir_updates = dir_updates
        self.hDir = win32file.CreateFile(str(path), winnt.FILE_LIST_DIRECTORY,
                                         win32con.FILE_SHARE_READ |
                                         win32con.FILE_SHARE_WRITE |
                                         win32con.FILE_SHARE_DELETE,
                                         None, win32con.OPEN_EXISTING,
                                         win32con.FILE_FLAG_BACKUP_SEMANTICS,
                                         None)
        for i in threading.Thread(target=self.watcher), \
                threading.Thread(target=self.notifier):
            i.daemon = True
            i.start()
        while forever:
            time.sleep(1)

    def watcher(self):
        while True:
            # handle, size, bWatchSubtree, dwNotifyFilter, overlapped
            for action, path in win32file.ReadDirectoryChangesW(
                    self.hDir, 8*1024, True, self.FILE_NOTIFY_CHANGE, None):
                with self.loader_lock:
                    act = self.actions[action]
                    if act == 'update' and not self.dir_updates \
                            and (self.path/path).is_dir():
                        continue
                    self.watch[path] = self.watch.get(path, []) + [act]

    def notifier(self):
        while True:
            with self.loader_lock:
                if len(self.watch):  # if update needed
                    for i in self.watch:
                        self.callback(i, self.watch[i])
                    self.watch.clear()
            time.sleep(0.25)


def dot_js(path):
    "Replace .py extension with .js"
    p = Path(path)
    if p.suffix.lower() == ".py":
        p = p.with_suffix(".js")
    return str(p)


def parse_manifest():
    file_names = []
    with open(dir_ext/manifest_file, 'r') as f:
        data = eval(f.read())
    for i in data.get("icons", {}).values():
        file_names.append(i)
    for action_type in ("browser_action", "action_type"):
        action = data.get(action_type, {})
        icon = action.get("default_icon")
        if icon:
            file_names += ([icon] if isinstance(icon, str)
                           else list(icon.values()))
        popup = action.get("default_popup")
        if popup:
            file_names.append(popup)
    background = data.get("background", {})
    scripts = background.get("scripts")
    if scripts:
        file_names += scripts
        data["background"]["scripts"] = [dot_js(i) for i in scripts]
    page = background.get("page")
    if page:
        file_names.append(page)
    for i, content_script in enumerate(data.get("content_scripts", [])):
        css = content_script.get("css")
        if css:
            file_names += css
        js = content_script.get("js")
        if js:
            file_names += js
            data["content_scripts"][i]["js"] = [dot_js(i) for i in js]
    devtools_page = data.get("devtools_page")
    if devtools_page:
        file_names.append(devtools_page)
    for i in data.get("nacl_modules", []):
        path = i.get("path")
        if path:
            file_names.append(path)
    options_page = data.get("options_page")
    if options_page:
        file_names.append(options_page)
    page = data.get("options_ui", {}).get("page")
    if page:
        file_names.append(page)
    for i in data.get("plugins", []):
        path = i.get("path")
        if path:
            file_names.append(path)
    file_names += data.get("sandbox", {}).get("pages", [])
    managed_schema = data.get("storage", {}).get("managed_schema")
    if managed_schema:
        file_names.append(managed_schema)
    file_names += data.get("web_accessible_resources", [])
    # pychromext settings:
    file_names += data.get('filelist', [])
    del data['filelist']
    return set(file_names), json.dumps(data)


def mkpath(path):
    "Create path, ignore if exists, retry on permission error"
    perm_error = True
    while perm_error:
        try:
            os.makedirs(path, exist_ok=True)
            return True
        except PermissionError:
            pass  # Retry
        except:
            raise


def build(filepath):
    filepath = Path(filepath)
    ext = filepath.suffix.lower()
    rel = filepath.relative_to(dir_ext)
    dest = output_dir/rel
#    print()
    if filepath.is_dir():
        print(time.strftime('%H:%M:%S'), "Create folder", rel, "...",
              flush=True, end='')
        res = mkpath(dest)
        print('done' if res else 'failed')
    elif ext == ".py":
        print(time.strftime('%H:%M:%S'), "Compiling", rel, "...", flush=True,
              end='')
        # FIXME: mkpath?
        res = compile_py(filepath, dest.with_suffix(".js"))
        print('done' if res else 'failed')
    elif ext in ('.html', '.htm'):
        print(time.strftime('%H:%M:%S'), "Compiling", rel, "...", flush=True,
              end='')
        mkpath(dest.parent)
        res = compile_html(filepath, dest)
        print('done' if res else 'failed')
    else:
        print(time.strftime('%H:%M:%S'), 'Copy', rel, "...", flush=True,
              end='')
        try:
            if ext == ".css":
                parse_css_urls(filepath)
            mkpath(dest.parent)
            shutil.copy(filepath, dest)
            print('done')
        except FileNotFoundError as e:
            print_err(e)
            print('failed')


def rebuild_all():
    global file_names
    skip_rebuildall = False
    print("REBUILD ALL")
    if not (dir_ext/manifest_file).exists():
        print_err(time.strftime('%H:%M:%S'),
                  "Cannot build. Manifest file not found", newline=False)
    else:
        file_names_, manifest_json = parse_manifest()
        if file_names_ == file_names:  # filelist not changed
            skip_rebuildall = True
        file_names = file_names_
        if not skip_rebuildall:
            try:
                shutil.rmtree(output_dir)
            except FileNotFoundError:
                pass
            mkpath(output_dir)
        with open((output_dir/manifest_file).with_suffix(".json"), 'w') as f:
            f.write(manifest_json)
            print(time.strftime('%H:%M:%S'), "Manifest file updated")
        if not skip_rebuildall:
            processed = []  # copy/compile each file only once
            for i in file_names:
                n = None
                for n, j in enumerate(glob.glob(str(dir_ext/i),
                                                recursive=True)):
                    if not Path(j).exists():
                        n = None
                    elif j not in processed:
                        build(j)
                        processed.append(j)
                if n is None:
                    print_err(time.strftime('%H:%M:%S'),
                              "Source path not found:", i, newline=False)
    print("--- finished ---")


def file_change(file_name, actions):
    if file_name == manifest_file:
        rebuild_all()
        return
    for i in file_names:
        if fnmatch.fnmatch(file_name, i):
            if actions[-1] in ('add', 'update', 'renamed_to'):
                build(dir_ext/file_name)
            else:
                print(time.strftime('%H:%M:%S'), "Remove", file_name)
                target = output_dir/file_name
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    os.remove(dot_js(target))
            break  # already found

rebuild_all()
if args.monitor:
    Monitor(dir_ext, file_change, forever=True)
