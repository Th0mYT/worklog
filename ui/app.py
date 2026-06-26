"""
worklog UI — pywebview desktop window.

Launch with:
    python -m ui.app
"""

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import webview

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
from config import Config  # noqa: E402
from logger.activity_poller import _system_idle_seconds  # noqa: E402

_CONFIG_PATH = Path.home() / '.worklog' / 'config.toml'

# ── HTML / CSS / JS (self-contained, no CDN) ─────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>worklog</title>
  <style>
    :root {
      --bg:            #f2f2f7;
      --surface:       #ffffff;
      --surface-alt:   #f5f5f7;
      --surface-raise: #ffffff;
      --text:          #1c1c1e;
      --text-sec:      #6e6e73;
      --text-ter:      #aeaeb2;
      --text-muted:    #3c3c43;
      --border:        #d1d1d6;
      --border-lt:     #e5e5ea;
      --sep:           #e5e5ea;
      --entry-hover:   rgba(0,0,0,.04);
      --btn-gray-bg:   #e8e8ed;
      --btn-gray-cl:   #3c3c43;
      --placeholder:   #c7c7cc;
      --toast-bg:      #1c1c1e;
      --card-shadow:   0 1px 4px rgba(0,0,0,.08), 0 0 0 .5px rgba(0,0,0,.05);
      --accent:        #007aff;
      --accent-ring:   rgba(0,122,255,.14);
      --repo-bg:       #f9f9fb;
      --repo-border:   #e5e5ea;
      --repo-input:    #ffffff;
      --ws-bg:         #ebebf0;
      --ws-color:      #8e8e93;
      --ws-border:     #d1d1d6;
      color-scheme:    light dark;
    }

    @media (prefers-color-scheme: dark) {
      :root {
        --bg:            #111113;
        --surface:       #1e1e20;
        --surface-alt:   #28282a;
        --surface-raise: #333335;
        --text:          #f2f2f7;
        --text-sec:      #8d8d93;
        --text-ter:      #58585c;
        --text-muted:    #e5e5ea;
        --border:        #3e3e42;
        --border-lt:     #2c2c2e;
        --sep:           #2c2c2e;
        --entry-hover:   rgba(255,255,255,.06);
        --btn-gray-bg:   #3a3a3c;
        --btn-gray-cl:   #e5e5ea;
        --placeholder:   #525256;
        --toast-bg:      #3a3a3c;
        --card-shadow:   none;
        --accent-ring:   rgba(0,122,255,.22);
        --repo-bg:       #28282a;
        --repo-border:   #3a3a3c;
        --repo-input:    #333335;
        --ws-bg:         #3a3a3c;
        --ws-color:      #8d8d93;
        --ws-border:     #4a4a4e;
      }
    }

    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    html, body {
      height: 100%;
    }

    body {
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
      background: var(--bg);
      color: var(--text);
      -webkit-user-select: none;
      user-select: none;
      overflow: hidden;
    }

    ::placeholder { color: var(--placeholder); }

    /* ── root wrapper ── */
    .wrap {
      display: flex;
      flex-direction: column;
      height: 100%;
      padding: 18px 18px 14px;
      gap: 10px;
    }

    /* ── views ── */
    .view {
      flex-direction: column;
      flex: 1;
      min-height: 0;
      gap: 10px;
    }

    /* settings view — scrolling is handled by .settings-grid inside */
    #view-settings { overflow: hidden; }

    /* ── header ── */
    .app-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-shrink: 0;
    }

    h1 { font-size: 22px; font-weight: 700; letter-spacing: -0.3px; }

    /* ── cards ── */
    .card {
      background: var(--surface);
      border-radius: 13px;
      padding: 15px 17px;
      box-shadow: var(--card-shadow);
      flex-shrink: 0;
    }

    /* cards that stretch to fill remaining space */
    .card-grow {
      flex: 1;
      min-height: 0;
      display: flex;
      flex-direction: column;
    }

    /* ── content area (logs + enrich) — side by side when the window is wide ── */
    .content-area {
      display: flex;
      flex-direction: column;
      flex: 1;
      min-height: 0;
      gap: 10px;
    }
    @media (min-width: 700px) {
      .content-area { flex-direction: row; }
    }

    /* ── settings grid ── */
    .settings-grid {
      display: flex;
      flex-direction: column;
      gap: 10px;
      flex: 1;
      min-height: 0;
      overflow-y: auto;
      padding-right: 2px;
    }

    /* two-column field rows inside a card */
    .field-row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }
    .field-row .field-group { margin-bottom: 0; }

    /* ── repo path list ── */
    .repo-list {
      display: flex; flex-direction: column; gap: 6px;
      max-height: 260px; overflow-y: auto;
      margin-bottom: 8px; padding-right: 3px;
    }
    .repo-row {
      display: flex; flex-direction: column; gap: 5px;
      padding: 9px 11px; flex-shrink: 0;
      background: var(--repo-bg);
      border: 1px solid var(--repo-border);
      border-left: 3px solid var(--accent);
      border-radius: 8px;
      transition: border-left-color .15s, background .15s, border-color .15s;
    }
    .repo-row.is-workspace {
      background: rgba(255,149,0,.05);
      border-color: rgba(255,149,0,.22);
      border-left-color: #ff9500;
    }
    .repo-row-top  { display: flex; align-items: center; gap: 6px; }
    .repo-row-meta { display: flex; align-items: center; gap: 6px; }
    .repo-row-top input[type=text] {
      flex: 1; min-width: 0;
      padding: 6px 8px;
      border: 1px solid var(--border); border-radius: 7px;
      font-size: 12px; font-family: "SF Mono", Menlo, monospace;
      background: var(--repo-input); color: var(--text);
      outline: none;
      -webkit-user-select: text; user-select: text;
    }
    .repo-row-top input[type=text]:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px var(--accent-ring);
    }
    .repo-row.is-workspace .repo-row-top input[type=text]:focus {
      border-color: #ff9500;
      box-shadow: 0 0 0 3px rgba(255,149,0,.14);
    }
    .repo-tags {
      flex: 1; min-width: 0;
      padding: 4px 7px;
      border: 1px solid var(--border-lt); border-radius: 5px;
      font-size: 11px; font-family: inherit;
      background: transparent; color: var(--text-muted);
      outline: none;
      -webkit-user-select: text; user-select: text;
    }
    .repo-tags::placeholder { color: var(--placeholder); font-style: italic; }
    .repo-tags:focus { border-color: var(--accent); background: var(--repo-input); }
    /* workspace toggle pill */
    .ws-label {
      display: inline-flex; align-items: center;
      font-size: 10px; font-weight: 600; letter-spacing: 0.2px;
      color: var(--ws-color);
      background: var(--ws-bg);
      border: 1px solid var(--ws-border);
      border-radius: 5px;
      padding: 3px 8px;
      cursor: pointer; white-space: nowrap; flex-shrink: 0;
      transition: color .12s, background .12s, border-color .12s;
      -webkit-user-select: none; user-select: none;
    }
    .ws-label input[type=checkbox] { display: none; }
    .ws-label.active {
      color: #c96000;
      background: rgba(255,149,0,.13);
      border-color: rgba(255,149,0,.38);
    }
    @media (prefers-color-scheme: dark) {
      .ws-label.active { color: #ffa733; }
    }
    .repo-remove {
      background: none; border: none;
      color: var(--placeholder); cursor: pointer;
      font-size: 16px; line-height: 1;
      padding: 0 2px; flex-shrink: 0;
      transition: color .12s;
    }
    .repo-remove:hover { color: #ff3b30; }
    .repo-add-btn {
      width: 100%; background: none;
      border: 1px dashed var(--border); border-radius: 8px;
      color: var(--text-sec); cursor: pointer;
      font-size: 12px; font-family: inherit;
      padding: 6px 10px; text-align: center;
      transition: border-color .12s, color .12s;
    }
    .repo-add-btn:hover { border-color: var(--accent); color: var(--accent); }

    /* ── section title ── */
    .section-title {
      font-size: 12px;
      font-weight: 600;
      color: var(--text-sec);
      text-transform: uppercase;
      letter-spacing: 0.4px;
      margin-bottom: 10px;
      flex-shrink: 0;
    }

    /* ── status ── */
    .status-row { display: flex; align-items: center; gap: 10px; }

    @keyframes pulse {
      0%, 100% { box-shadow: 0 0 0 0 rgba(52,199,89,.5); }
      60%       { box-shadow: 0 0 0 5px rgba(52,199,89,0); }
    }

    .dot {
      width: 11px; height: 11px; border-radius: 50%;
      background: var(--text-ter);
      transition: background .3s;
      flex-shrink: 0;
    }
    .dot.on {
      background: #34c759;
      animation: pulse 2s ease-in-out infinite;
    }

    @keyframes pulse-pause {
      0%, 100% { box-shadow: 0 0 0 0 rgba(255,149,0,.5); }
      60%       { box-shadow: 0 0 0 5px rgba(255,149,0,0); }
    }
    .dot.paused {
      background: #ff9500;
      animation: pulse-pause 3s ease-in-out infinite;
    }

    #status-text { font-size: 17px; font-weight: 600; }

    .meta { font-size: 12px; color: var(--text-sec); margin-top: 5px; line-height: 1.5; }

    /* ── buttons ── */
    .btn-row {
      display: flex;
      gap: 8px;
      margin-top: 13px;
      flex-wrap: wrap;
      flex-shrink: 0;
      row-gap: 6px;
    }

    button {
      padding: 8px 20px;
      border: none; border-radius: 9px;
      font-size: 13px; font-weight: 500;
      font-family: inherit;
      cursor: pointer;
      color: #fff;
      transition: filter .12s, opacity .12s;
    }
    button:hover:not(:disabled)  { filter: brightness(.88); }
    button:active:not(:disabled) { filter: brightness(.76); }
    button:disabled { opacity: .45; cursor: default; filter: none !important; }

    .btn-green  { background: #34c759; }
    .btn-red    { background: #ff3b30; }
    .btn-orange { background: #ff9500; }
    .btn-gray   { background: var(--btn-gray-bg); color: var(--btn-gray-cl); }
    .btn-blue   { background: #007aff; }

    /* ── log list ── */
    #log-list {
      flex: 1;
      min-height: 0;
      overflow-y: auto;
      margin-bottom: 12px;
    }

    .log-entry {
      padding: 7px 0;
      border-bottom: 1px solid var(--sep);
    }
    .log-entry:last-child { border-bottom: none; }

    .log-entry-head {
      display: flex;
      align-items: center;
      gap: 6px;
      margin-bottom: 2px;
    }
    .log-entry-date {
      font-size: 13px;
      font-weight: 600;
      color: var(--text);
      flex: 1;
    }
    .log-today {
      font-size: 10px; font-weight: 600;
      color: #fff; background: #34c759;
      border-radius: 4px; padding: 1px 6px;
      flex-shrink: 0;
    }
    .log-entry-meta {
      font-size: 11px;
      color: var(--text-sec);
      margin-bottom: 5px;
    }
    .log-type-pills {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
    }
    .type-pill {
      font-size: 10px;
      font-weight: 500;
      padding: 2px 7px;
      border-radius: 10px;
      white-space: nowrap;
    }
    /* Semantic pill colours — light */
    .pill-coding        { color: #0060df; background: rgba(0,122,255,.10); }
    .pill-browser       { color: #b85c00; background: rgba(255,149,0,.10); }
    .pill-meeting       { color: #4745b0; background: rgba(88,86,214,.10); }
    .pill-comm          { color: #0f7fa0; background: rgba(50,173,230,.10); }
    .pill-design        { color: #b51f41; background: rgba(255,45,85,.10); }
    .pill-productivity  { color: #1a7a35; background: rgba(52,199,89,.10); }
    .pill-git           { color: #636366; background: rgba(99,99,102,.10); }
    .pill-other         { color: #8e8e93; background: rgba(142,142,147,.10); }
    @media (prefers-color-scheme: dark) {
      .pill-coding        { color: #5aadff; background: rgba(0,122,255,.18); }
      .pill-browser       { color: #ffb340; background: rgba(255,149,0,.18); }
      .pill-meeting       { color: #9392ec; background: rgba(88,86,214,.18); }
      .pill-comm          { color: #5ed2f0; background: rgba(50,173,230,.18); }
      .pill-design        { color: #ff7295; background: rgba(255,45,85,.18); }
      .pill-productivity  { color: #59d47a; background: rgba(52,199,89,.18); }
      .pill-git           { color: #9d9da2; background: rgba(142,142,147,.18); }
      .pill-other         { color: #8d8d93; background: rgba(142,142,147,.14); }
    }

    /* ── date navigator ── */
    .date-nav {
      display: flex;
      align-items: center;
      gap: 6px;
      flex-shrink: 0;
      background: var(--surface);
      border-radius: 13px;
      padding: 8px 12px;
      box-shadow: var(--card-shadow);
    }
    .date-nav-btn {
      background: var(--entry-hover);
      border: none;
      border-radius: 7px;
      width: 28px; height: 28px;
      font-size: 15px; line-height: 1;
      cursor: pointer;
      color: var(--text-muted);
      display: flex; align-items: center; justify-content: center;
      flex-shrink: 0;
      transition: filter .12s;
    }
    .date-nav-btn:hover:not(:disabled)  { filter: brightness(.9); }
    .date-nav-btn:active:not(:disabled) { filter: brightness(.8); }
    .date-nav-btn:disabled { opacity: .35; cursor: default; }
    #action-date {
      flex: 1;
      border: none;
      background: transparent;
      font-size: 14px;
      font-weight: 600;
      color: var(--text);
      font-family: inherit;
      text-align: center;
      outline: none;
      cursor: pointer;
      -webkit-user-select: text; user-select: text;
    }

    .no-logs { font-size: 12px; color: var(--text-sec); padding: 4px 0; }

    /* clickable log rows */
    .log-entry {
      cursor: pointer;
      border-radius: 7px;
      padding: 7px 8px;
      margin: 0 -8px;
      transition: background .1s;
    }
    .log-entry:hover { background: var(--entry-hover); }
    .log-entry:last-child { border-bottom: none; }

    /* ── log detail view ── */
    #detail-list {
      flex: 1;
      min-height: 0;
      overflow-y: auto;
    }
    .detail-entry {
      display: flex;
      align-items: flex-start;
      gap: 8px;
      padding: 6px 0;
      border-bottom: 1px solid var(--sep);
      position: relative;
    }
    .detail-entry:last-child { border-bottom: none; }
    .detail-entry .entry-del {
      opacity: 0;
      flex-shrink: 0;
      background: none;
      border: none;
      cursor: pointer;
      font-size: 14px;
      color: var(--text-ter);
      padding: 0 2px;
      line-height: 1;
      transition: opacity 0.15s, color 0.15s;
      align-self: center;
    }
    .detail-entry:hover .entry-del { opacity: 1; }
    .detail-entry .entry-del:hover { color: var(--danger, #e05252); }
    .detail-time {
      font-family: "SF Mono", Menlo, monospace;
      font-size: 11px;
      color: var(--text-ter);
      flex-shrink: 0;
      padding-top: 3px;
      min-width: 36px;
    }
    .detail-text {
      flex: 1;
      min-width: 0;
      font-size: 12px;
      color: var(--text);
      line-height: 1.45;
      word-break: break-word;
    }
    .detail-sub {
      font-size: 11px;
      color: var(--text-sec);
      margin-top: 1px;
    }
    .detail-stats {
      font-family: "SF Mono", Menlo, monospace;
      font-size: 10px;
      color: var(--text-sec);
    }

    /* ── log card footer ── */
    .log-footer {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-shrink: 0;
      padding-top: 10px;
      margin-top: 10px;
      border-top: 1px solid var(--sep);
      gap: 8px;
    }
    .log-footer-left  { display: flex; align-items: center; gap: 8px; }
    .log-footer-right { display: flex; align-items: center; gap: 2px; }

    /* ghost buttons — no fill, colour appears only on hover */
    .btn-ghost {
      background: none;
      color: var(--text-ter);
      font-size: 11.5px;
      font-weight: 500;
      padding: 4px 8px;
      border-radius: 6px;
      transition: color .12s, background .12s;
    }
    .btn-ghost:hover:not(:disabled) { filter: none; }
    .btn-ghost-warn:hover:not(:disabled) {
      color: #c96000;
      background: rgba(255,149,0,.10);
    }
    .btn-ghost-danger:hover:not(:disabled) {
      color: #ff3b30;
      background: rgba(255,59,48,.09);
    }
    @media (prefers-color-scheme: dark) {
      .btn-ghost-warn:hover:not(:disabled)   { color: #ffa733; }
      .btn-ghost-danger:hover:not(:disabled) { color: #ff6961; }
    }

    /* small-size modifier for solid buttons used in footers */
    .btn-sm { padding: 5px 13px !important; font-size: 12px !important; }

    /* ── subsections inside a card (kept for log detail view) ── */
    .subsection {
      flex-shrink: 0;
      padding-top: 11px;
      margin-top: 11px;
      border-top: 1px solid var(--sep);
    }
    .subsection-title {
      font-size: 10px;
      font-weight: 700;
      color: var(--text-ter);
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-bottom: 8px;
    }

    /* ── result label ── */
    .result-label {
      font-size: 10px;
      font-weight: 700;
      color: var(--text-ter);
      text-transform: uppercase;
      letter-spacing: 0.5px;
      padding: 6px 11px 2px;
      flex-shrink: 0;
    }

    /* ── footer ── */
    .footer {
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      flex-shrink: 0;
    }

    /* ── settings ── */
    .field-group { margin-bottom: 12px; }
    .field-group:last-child { margin-bottom: 0; }

    .field-label {
      display: block;
      font-size: 11px; font-weight: 600;
      color: var(--text-sec);
      text-transform: uppercase; letter-spacing: 0.3px;
      margin-bottom: 4px;
    }

    .field-group input,
    .field-group select,
    .field-group textarea {
      width: 100%;
      padding: 8px 10px;
      border: 1px solid var(--border);
      border-radius: 8px;
      font-size: 13px; font-family: inherit;
      background: var(--surface-alt); color: var(--text);
      outline: none; transition: border-color .12s, box-shadow .12s;
      -webkit-user-select: text; user-select: text;
    }
    .field-group input:focus,
    .field-group select:focus,
    .field-group textarea:focus {
      border-color: var(--accent); background: var(--surface-raise);
      box-shadow: 0 0 0 3px var(--accent-ring);
    }
    .field-group textarea { resize: vertical; min-height: 60px; line-height: 1.5; }
    .field-hint { font-size: 11px; color: var(--text-sec); margin-top: 3px; }

/* ── action card ── */
    .action-row {
      display: flex; align-items: center;
      gap: 8px; margin-bottom: 10px;
      flex-shrink: 0; flex-wrap: wrap;
    }

    /* buttons inside action rows never shrink below their label */
    .action-row button { flex-shrink: 0; }

    .dry-label {
      display: flex; align-items: center; gap: 5px;
      font-size: 12px; color: var(--text-muted); cursor: pointer;
      white-space: nowrap; flex-shrink: 0;
    }
    .dry-label input[type=checkbox] { width: auto; margin: 0; cursor: pointer; }

    input.inline, select.inline {
      flex: 1; min-width: 100px;
      padding: 7px 10px;
      border: 1px solid var(--border);
      border-radius: 8px;
      font-size: 12px; font-family: inherit;
      background: var(--surface-alt); color: var(--text);
      outline: none;
    }
    input.inline { -webkit-user-select: text; user-select: text; }
    input.inline:focus, select.inline:focus {
      border-color: var(--accent); background: var(--surface-raise);
      box-shadow: 0 0 0 3px var(--accent-ring);
    }

    /* output area — grows to fill card when visible */
    #action-result {
      display: none;
      flex-direction: column;
      flex: 1;
      min-height: 0;
      margin-top: 10px;
    }
    #action-result.visible { display: flex; }

    .result-box {
      display: flex; flex-direction: column;
      flex: 1; min-height: 0;
      border: 1px solid var(--border-lt);
      border-radius: 8px;
      background: var(--surface-alt);
      overflow: hidden;
    }

    .result-pre {
      flex: 1; min-height: 0;
      font-family: "SF Mono", Menlo, monospace;
      font-size: 11.5px; color: var(--text-muted);
      line-height: 1.65;
      padding: 11px 13px;
      overflow-y: auto;
      white-space: pre-wrap;
      word-break: break-word;
      margin: 0;
      -webkit-user-select: text; user-select: text;
    }
    .result-pre.err         { color: #ff3b30; }
    .result-pre.placeholder { color: var(--placeholder); font-style: italic; }

    /* ── summarizer progress bar ── */
    .progress-wrap {
      flex-shrink: 0;
      padding: 0 0 10px;
      display: none;
    }
    .progress-wrap.visible { display: block; }
    .progress-track {
      height: 3px;
      background: var(--border-lt);
      border-radius: 2px;
      overflow: hidden;
      margin-bottom: 6px;
    }
    @keyframes progress-slide {
      0%   { transform: translateX(-120%); }
      100% { transform: translateX(420%); }
    }
    .progress-fill {
      height: 100%;
      width: 28%;
      background: linear-gradient(90deg, #007aff, #5ac8fa);
      border-radius: 2px;
      animation: progress-slide 1.3s cubic-bezier(.4,0,.6,1) infinite;
    }
    .progress-meta {
      display: flex;
      justify-content: space-between;
      font-size: 10.5px;
      color: var(--text-ter);
    }

    /* ── structured summary output ── */
    .summary-output {
      flex: 1;
      min-height: 0;
      overflow-y: auto;
    }
    .summary-row {
      display: flex;
      align-items: baseline;
      gap: 7px;
      padding: 6px 0;
      border-bottom: 1px solid var(--sep);
      font-size: 12.5px;
    }
    .summary-row:last-of-type { border-bottom: none; }
    .summary-desc {
      flex: 1;
      min-width: 0;
      color: var(--text);
      line-height: 1.4;
    }
    .summary-ref {
      font-size: 10.5px;
      color: var(--text-ter);
      font-family: "SF Mono", Menlo, monospace;
      white-space: nowrap;
      flex-shrink: 0;
    }
    .summary-dur {
      font-size: 11.5px;
      font-weight: 600;
      color: var(--accent);
      white-space: nowrap;
      flex-shrink: 0;
      font-family: "SF Mono", Menlo, monospace;
    }
    .summary-section-hdr {
      font-size: 10px;
      font-weight: 700;
      color: var(--text-ter);
      text-transform: uppercase;
      letter-spacing: 0.5px;
      padding: 10px 0 5px;
    }
    .summary-section-hdr:first-child { padding-top: 2px; }
    .summary-total {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 8px 0 2px;
      margin-top: 4px;
      border-top: 1px solid var(--sep);
      font-size: 12.5px;
      font-weight: 600;
      color: var(--text-sec);
    }
    .summary-total span:last-child {
      color: var(--text);
      font-family: "SF Mono", Menlo, monospace;
    }
    .summary-plain {
      font-size: 12px;
      color: var(--text-sec);
      padding: 3px 0;
      line-height: 1.5;
    }

    .result-footer {
      display: flex; justify-content: flex-end;
      padding: 5px 8px;
      border-top: 1px solid var(--border-lt);
      flex-shrink: 0;
    }
    .btn-xs { padding: 3px 12px; font-size: 11px; }

    /* toggled-on state for the Prompt button */
    .btn-active {
      background: rgba(0,122,255,.12) !important;
      color: var(--accent) !important;
      filter: none !important;
    }
    @media (prefers-color-scheme: dark) {
      .btn-active { background: rgba(0,122,255,.22) !important; color: #5aadff !important; }
    }

    /* ── toast ── */
    #toast {
      position: fixed;
      bottom: 18px; left: 50%;
      transform: translateX(-50%) translateY(8px);
      background: var(--toast-bg); color: #fff;
      padding: 7px 16px;
      border-radius: 20px;
      font-size: 13px; font-weight: 500;
      opacity: 0;
      transition: opacity .18s, transform .18s;
      pointer-events: none;
      z-index: 999;
      white-space: nowrap;
    }
    #toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }
    #toast.toast-error { background: #ff3b30; }
    #toast.toast-ok    { background: #34c759; }

    /* ── detail header ── */
    #detail-date  { font-size: 15px; font-weight: 600; color: var(--text); }
    #detail-count { font-size: 12px; color: var(--text-sec); }

    /* ── inline code ── */
    code {
      font-family: "SF Mono", Menlo, monospace;
      font-size: 10.5px;
      background: var(--surface-alt);
      border: 1px solid var(--border-lt);
      border-radius: 4px;
      padding: 1px 5px;
      color: var(--text-muted);
    }

    /* ── thin custom scrollbars ── */
    ::-webkit-scrollbar { width: 5px; height: 5px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb {
      background: var(--border);
      border-radius: 3px;
    }
    ::-webkit-scrollbar-thumb:hover { background: var(--text-ter); }
  </style>
</head>
<body>
<div class="wrap">

  <!-- ════════════════════ MAIN VIEW ════════════════════ -->
  <div id="view-main" class="view" style="display:flex">

    <div class="app-header">
      <h1>worklog</h1>
      <div style="display:flex;gap:6px">
        <button class="btn-gray" style="padding:5px 13px;font-size:12px" onclick="showSettings()">Settings</button>
        <button class="btn-gray" style="padding:5px 13px;font-size:12px" onclick="quitApp()">Quit</button>
      </div>
    </div>

    <!-- status -->
    <div class="card">
      <div class="status-row">
        <div class="dot" id="dot"></div>
        <span id="status-text">—</span>
      </div>
      <div class="meta" id="last-ts"></div>
      <div class="meta" id="count"></div>
      <div class="btn-row">
        <button class="btn-green" id="btn-start" onclick="startPoller()">Start</button>
        <button class="btn-red"   id="btn-stop"  onclick="stopPoller()">Stop</button>
      </div>
    </div>

    <!-- shared date navigator -->
    <div class="date-nav">
      <button class="date-nav-btn" id="btn-date-prev" onclick="stepDate(-1)" title="Previous day">‹</button>
      <input type="date" id="action-date" onchange="onDateChange()">
      <button class="date-nav-btn" id="btn-date-next" onclick="stepDate(1)"  title="Next day">›</button>
    </div>

    <div class="content-area">

      <!-- logs -->
      <div class="card card-grow">
        <div class="section-title">Logs</div>
        <div id="log-list"><p class="no-logs">Loading…</p></div>

        <div class="log-footer">
          <div class="log-footer-left">
            <label class="dry-label">
              <input type="checkbox" id="enrich-dry"> Dry run
            </label>
            <button class="btn-ghost" id="btn-enrich" onclick="runEnrich()">Enrich</button>
          </div>
          <div class="log-footer-right">
            <button class="btn-ghost btn-ghost-warn"   id="btn-reset"  onclick="resetToday()">Reset today</button>
            <button class="btn-ghost btn-ghost-danger" id="btn-delete" onclick="deleteAll()">Delete all</button>
          </div>
        </div>
      </div>

      <!-- summary -->
      <div class="card card-grow">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;flex-shrink:0">
          <div class="section-title" style="margin-bottom:0">Daily Summary</div>
          <span id="result-label" style="font-size:10px;font-weight:700;color:var(--text-ter);text-transform:uppercase;letter-spacing:0.5px"></span>
        </div>

        <div class="progress-wrap" id="summary-progress">
          <div class="progress-track"><div class="progress-fill"></div></div>
          <div class="progress-meta">
            <span id="progress-backend">Calling LLM…</span>
            <span style="display:flex;align-items:center;gap:8px">
              <span id="progress-elapsed"></span>
              <button class="btn-ghost" style="padding:1px 8px;font-size:11px" onclick="cancelSummary()">Cancel</button>
            </span>
          </div>
        </div>

        <div id="summary-output" class="summary-output">
          <p class="no-logs">Generate a summary to see it here…</p>
        </div>

        <div class="log-footer">
          <div class="log-footer-left">
            <button class="btn-blue btn-sm" id="btn-summary" onclick="runSummary()">Generate</button>
            <button class="btn-gray btn-sm" id="btn-preview-prompt" onclick="previewPrompt()">Prompt</button>
            <select class="inline" id="sum-backend" style="min-width:90px">
              <option value="">Default</option>
              <option value="ollama">Ollama</option>
              <option value="council">Council (experimental)</option>
              <option value="claude">Claude CLI</option>
              <option value="anthropic">Anthropic</option>
              <option value="openai">OpenAI</option>
            </select>
          </div>
          <div class="log-footer-right">
            <button class="btn-ghost" onclick="copyOutput()">Copy</button>
          </div>
        </div>
      </div>

    </div><!-- /content-area -->

  </div><!-- /view-main -->

  <!-- ════════════════════ SETTINGS VIEW ════════════════════ -->
  <div id="view-settings" class="view" style="display:none">

    <div class="app-header" style="flex-shrink:0">
      <button class="btn-gray"  style="padding:5px 13px;font-size:12px" id="btn-cancel" onclick="showMain()">← Back</button>
      <h1 id="settings-title">Settings</h1>
      <button class="btn-green" style="padding:5px 13px;font-size:12px" id="btn-save"   onclick="saveSettings()">Save</button>
    </div>

    <div class="settings-grid">

      <!-- ── Poller ── -->
      <div class="card">
        <div class="section-title">Poller</div>
        <div class="field-group">
          <label class="field-label" for="s-logs-dir">Logs Directory</label>
          <input type="text" id="s-logs-dir" placeholder="~/.worklog/logs">
          <div class="field-hint">Daily .jsonl files are written here</div>
        </div>
        <div class="field-group">
          <label class="field-label" for="s-poll-interval">Poll Interval</label>
          <div style="display:flex;align-items:center;gap:8px">
            <input type="number" id="s-poll-interval" min="30" max="3600" placeholder="300">
            <span style="font-size:12px;color:var(--text-sec);white-space:nowrap">seconds</span>
          </div>
        </div>
        <div class="field-group" style="margin-bottom:0">
          <label class="field-label" for="s-inactivity">Inactivity Timeout</label>
          <div style="display:flex;align-items:center;gap:8px">
            <input type="number" id="s-inactivity" min="60" max="7200" placeholder="300">
            <span style="font-size:12px;color:var(--text-sec);white-space:nowrap">seconds</span>
          </div>
          <div class="field-hint">Auto-pause after this many seconds of no keyboard/mouse activity</div>
        </div>
      </div>

      <!-- ── Git Enricher ── -->
      <div class="card">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;flex-shrink:0">
          <div class="section-title" style="margin-bottom:0">Git Enricher</div>
          <div style="display:flex;align-items:center;gap:8px">
            <label class="field-label" for="s-git-author" style="margin-bottom:0;white-space:nowrap">Author filter</label>
            <input type="text" id="s-git-author" placeholder="name or email"
                   style="width:180px;padding:6px 9px;border:1px solid var(--border);border-radius:8px;font-size:12px;font-family:inherit;background:var(--surface-alt);color:var(--text);outline:none;-webkit-user-select:text;user-select:text">
          </div>
        </div>
        <div class="field-group" style="margin-bottom:0">
          <div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:6px">
            <label class="field-label" style="margin-bottom:0">Paths</label>
            <span class="field-hint" style="margin-top:0">Toggle <b>workspace</b> to scan all repos inside a directory</span>
          </div>
          <div id="repo-list" class="repo-list"></div>
          <button class="repo-add-btn" onclick="addRepoRow()" style="margin-top:4px">+ Add path</button>
        </div>
      </div>

      <!-- ── Summarizer ── -->
      <div class="card">
        <div class="section-title">Summarizer</div>
        <div class="field-group">
          <label class="field-label" for="s-backend">Backend</label>
          <select id="s-backend" onchange="updateBackendFields()">
            <option value="ollama">Ollama</option>
            <option value="council">Council (experimental)</option>
            <option value="claude">Claude CLI</option>
            <option value="openai">OpenAI</option>
          </select>
        </div>

        <div id="ollama-fields">
          <div class="field-row">
            <div class="field-group">
              <label class="field-label" for="s-ollama-url">URL</label>
              <input type="text" id="s-ollama-url" placeholder="http://localhost:11434">
            </div>
            <div class="field-group">
              <label class="field-label" for="s-ollama-model">Model</label>
              <input type="text" id="s-ollama-model" placeholder="qwen2.5:7b">
            </div>
          </div>
        </div>

        <div id="council-fields" style="display:none">
          <div class="field-group" style="margin-bottom:0">
            <label class="field-label" for="s-council-url">URL</label>
            <input type="text" id="s-council-url" placeholder="http://localhost:8001">
          </div>
        </div>

        <div id="claude-fields" style="display:none">
          <div class="field-group" style="margin-bottom:0">
            <label class="field-label" for="s-claude-model">Model</label>
            <input type="text" id="s-claude-model" placeholder="claude-sonnet-4-6 (optional)">
            <div class="field-hint">Leave blank for CLI default · requires <code>claude</code> on PATH</div>
          </div>
        </div>

        <div id="openai-fields" style="display:none">
          <div class="field-group">
            <label class="field-label" for="s-openai-key">API Key</label>
            <input type="password" id="s-openai-key" placeholder="sk-…" autocomplete="off">
            <div class="field-hint">Stored in ~/.worklog/config.toml (not synced)</div>
          </div>
          <div class="field-group" style="margin-bottom:0">
            <label class="field-label" for="s-openai-model">Model</label>
            <input type="text" id="s-openai-model" placeholder="gpt-4o-mini">
          </div>
        </div>
      </div>

    </div><!-- /settings-grid -->

  </div><!-- /view-settings -->

  <!-- ════════════════════ LOG DETAIL VIEW ════════════════════ -->
  <div id="view-log-detail" class="view" style="display:none">
    <div class="app-header" style="flex-shrink:0">
      <button class="btn-gray" style="padding:5px 13px;font-size:12px" onclick="backFromDetail()">← Back</button>
      <span id="detail-date"></span>
      <span id="detail-count"></span>
    </div>
    <div class="card card-grow">
      <div id="detail-list"></div>
    </div>
  </div><!-- /view-log-detail -->

</div><!-- /wrap -->
<div id="toast"></div>

<script>
  let api = null;
  let _refreshTimer = null;
  let _onboarding = false;
  let _toastTimer = null;
  let _progressTimer = null;
  let _progressStart = 0;
  let _promptVisible = false;
  let _lastOutput = '';

  // ── toast ───────────────────────────────────────────────────────────────────

  function showToast(msg, type) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.className = type ? 'toast-' + type : '';
    void t.offsetWidth;
    t.classList.add('show');
    clearTimeout(_toastTimer);
    _toastTimer = setTimeout(() => t.classList.remove('show'), 2400);
  }

  // ── relative time ───────────────────────────────────────────────────────────

  function timeAgo(ts) {
    if (!ts) return '';
    const diff = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
    if (isNaN(diff) || diff < 0) return ts;
    if (diff < 60)    return 'just now';
    if (diff < 3600)  return Math.floor(diff / 60) + ' min ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    return ts;
  }

  // ── button lock helper ──────────────────────────────────────────────────────

  function withBtn(id, label, fn) {
    const btn = document.getElementById(id);
    const orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = label;
    return fn().finally(() => {
      btn.disabled = false;
      btn.textContent = orig;
    });
  }

  // ── date navigator ──────────────────────────────────────────────────────────

  function todayStr() {
    return new Date().toISOString().slice(0, 10);
  }

  function setDate(iso) {
    const today = todayStr();
    const d = iso > today ? today : iso;
    document.getElementById('action-date').value = d;
    document.getElementById('btn-date-next').disabled = (d >= today);
  }

  function stepDate(delta) {
    const cur = document.getElementById('action-date').value || todayStr();
    const d = new Date(cur + 'T12:00:00');
    d.setDate(d.getDate() + delta);
    setDate(d.toISOString().slice(0, 10));
    loadSavedSummary(document.getElementById('action-date').value);
  }

  function onDateChange() {
    setDate(document.getElementById('action-date').value);
    loadSavedSummary(document.getElementById('action-date').value);
  }

  // Show the saved summary for a date, or clear the panel if none exists.
  async function loadSavedSummary(date) {
    if (!api) return;
    try {
      const r = await api.get_summary(date);
      if (r && r.exists) {
        showOutput(r.output, true, 'Summary — saved ' + r.saved_at);
      } else {
        showOutput('', true, '');
      }
    } catch (e) { /* leave panel as-is on error */ }
  }

  // ── repo path list ──────────────────────────────────────────────────────────

  function addRepoRow(path = '', isWs = false, tags = []) {
    const list = document.getElementById('repo-list');
    const row  = document.createElement('div');
    row.className = 'repo-row' + (isWs ? ' is-workspace' : '');
    const tagsVal = Array.isArray(tags) ? tags.join(', ') : (tags || '');
    row.innerHTML = `
      <div class="repo-row-top">
        <input type="text" class="repo-path" placeholder="/path/to/repo or ~/workspace">
        <button class="repo-remove" title="Remove">×</button>
      </div>
      <div class="repo-row-meta">
        <input type="text" class="repo-tags" placeholder="tags: work, client-a, backend">
        <label class="ws-label${isWs ? ' active' : ''}">
          <input type="checkbox" class="repo-ws"> workspace
        </label>
      </div>
    `;
    const pathInp = row.querySelector('.repo-path');
    const tagsInp = row.querySelector('.repo-tags');
    const chk     = row.querySelector('.repo-ws');
    const lbl     = row.querySelector('.ws-label');
    pathInp.value  = path;
    tagsInp.value  = tagsVal;
    chk.checked    = isWs;
    chk.addEventListener('change', () => {
      lbl.classList.toggle('active', chk.checked);
      row.classList.toggle('is-workspace', chk.checked);
    });
    row.querySelector('.repo-remove').onclick = () => row.remove();
    list.appendChild(row);
    return row;
  }

  function getPaths() {
    return [...document.querySelectorAll('#repo-list .repo-row')].map(row => ({
      path:      (row.querySelector('.repo-path')?.value || '').trim(),
      workspace: row.querySelector('.repo-ws')?.checked || false,
      tags:      (row.querySelector('.repo-tags')?.value || '')
                   .split(',').map(t => t.trim()).filter(Boolean),
    })).filter(r => r.path);
  }

  // ── view navigation ─────────────────────────────────────────────────────────

  async function showSettings(onboarding) {
    _onboarding = !!onboarding;
    clearInterval(_refreshTimer);
    _refreshTimer = null;

    const s = await api.get_settings();
    document.getElementById('s-logs-dir').value      = s.logs_dir || '';
    document.getElementById('s-poll-interval').value = s.poll_interval || 300;
    document.getElementById('s-inactivity').value    = s.inactivity_timeout || 300;
    document.getElementById('s-git-author').value = s.git_author || '';
    document.getElementById('repo-list').innerHTML = '';
    const gitPaths = s.git_paths || [];
    gitPaths.forEach(p => addRepoRow(p.path, p.workspace, p.tags));
    if (!gitPaths.length) addRepoRow();
    document.getElementById('s-backend').value       = s.summarizer_backend || 'ollama';
    document.getElementById('s-ollama-url').value    = s.ollama_url || '';
    document.getElementById('s-ollama-model').value  = s.ollama_model || '';
    document.getElementById('s-council-url').value   = s.council_url || '';
    document.getElementById('s-claude-model').value  = s.claude_model || '';
    document.getElementById('s-openai-key').value    = s.openai_api_key || '';
    document.getElementById('s-openai-model').value  = s.openai_model || '';
    updateBackendFields();

    document.getElementById('settings-title').textContent = _onboarding ? 'Setup' : 'Settings';
    document.getElementById('btn-cancel').style.visibility = _onboarding ? 'hidden' : '';

    document.getElementById('view-main').style.display     = 'none';
    document.getElementById('view-settings').style.display = 'flex';
  }

  function showMain() {
    document.getElementById('view-settings').style.display  = 'none';
    document.getElementById('view-log-detail').style.display = 'none';
    document.getElementById('view-main').style.display      = 'flex';
    setDate(todayStr());
    loadSavedSummary(todayStr());
    refresh();
    _refreshTimer = setInterval(refresh, 1000);
  }

  function updateBackendFields() {
    const v = document.getElementById('s-backend').value;
    document.getElementById('ollama-fields').style.display  = v === 'ollama'  ? '' : 'none';
    document.getElementById('council-fields').style.display = v === 'council' ? '' : 'none';
    document.getElementById('claude-fields').style.display  = v === 'claude'  ? '' : 'none';
    document.getElementById('openai-fields').style.display  = v === 'openai'  ? '' : 'none';
  }

  // ── settings save ───────────────────────────────────────────────────────────

  async function saveSettings() {
    await withBtn('btn-save', 'Saving…', async () => {
      try {
        await api.save_settings({
          logs_dir:             document.getElementById('s-logs-dir').value.trim(),
          poll_interval:        parseInt(document.getElementById('s-poll-interval').value) || 300,
          inactivity_timeout:   parseInt(document.getElementById('s-inactivity').value) || 300,
          git_author:         document.getElementById('s-git-author').value.trim(),
          git_paths:          getPaths(),
          summarizer_backend: document.getElementById('s-backend').value,
          ollama_url:         document.getElementById('s-ollama-url').value.trim(),
          ollama_model:       document.getElementById('s-ollama-model').value.trim(),
          council_url:        document.getElementById('s-council-url').value.trim(),
          claude_model:       document.getElementById('s-claude-model').value.trim(),
          openai_api_key:     document.getElementById('s-openai-key').value.trim(),
          openai_model:       document.getElementById('s-openai-model').value.trim(),
        });
        showMain();
        showToast('Settings saved', 'ok');
      } catch (e) {
        showToast('Failed to save settings', 'error');
      }
    });
  }

  // ── refresh ─────────────────────────────────────────────────────────────────

  async function refresh() {
    if (!api) return;
    try {
      const [s, l] = await Promise.all([api.status(), api.logs()]);

      const isAutoPaused = s.auto_paused;
      document.getElementById('dot').className =
        'dot' + (s.running ? ' on' : isAutoPaused ? ' paused' : '');
      document.getElementById('status-text').textContent =
        s.running ? 'Active' : (isAutoPaused ? 'Paused' : 'Stopped');

      let metaText;
      if (s.running) {
        const remaining = s.inactivity_timeout - s.idle_seconds;
        if (remaining <= 60) {
          metaText = 'Idle · pausing in ' + Math.max(0, remaining) + 's';
        } else {
          metaText = s.last_ts ? 'Last capture: ' + timeAgo(s.last_ts) : 'No captures yet today';
        }
      } else if (isAutoPaused) {
        metaText = 'Paused · resumes on activity';
      } else {
        metaText = s.last_ts ? 'Last capture: ' + timeAgo(s.last_ts) : '';
      }
      document.getElementById('last-ts').textContent = metaText;
      document.getElementById('count').textContent =
        'Today: ' + s.count + (s.count === 1 ? ' entry' : ' entries');

      document.getElementById('btn-start').disabled = s.running;
      document.getElementById('btn-stop').disabled  = !s.running && !isAutoPaused;

      const list = document.getElementById('log-list');
      if (!l.logs.length) {
        list.innerHTML = '<p class="no-logs">No log files yet.</p>';
      } else {
        list.innerHTML = l.logs.map(f => {
          const range = (f.first_ts && f.last_ts) ? `${f.first_ts}–${f.last_ts}` : '';
          const parts = [
            range,
            f.count + (f.count === 1 ? ' entry' : ' entries'),
            f.git_count ? f.git_count + (f.git_count === 1 ? ' commit' : ' commits') : '',
          ].filter(Boolean).join(' · ');
          const pills = typesPills(f.types);
          return (
            `<div class="log-entry" onclick="showLogDetail('${f.date}')">` +
              '<div class="log-entry-head">' +
                '<span class="log-entry-date">' + f.date + '</span>' +
                (f.today ? '<span class="log-today">today</span>' : '') +
              '</div>' +
              '<div class="log-entry-meta">' + parts + '</div>' +
              (pills ? '<div class="log-type-pills">' + pills + '</div>' : '') +
            '</div>'
          );
        }).join('');
      }
    } catch (_) {}
  }

  // ── poller ──────────────────────────────────────────────────────────────────

  async function startPoller() {
    await withBtn('btn-start', 'Starting…', async () => {
      try {
        await api.start();
        await refresh();
        showToast('Poller started', 'ok');
      } catch (e) { showToast('Failed to start', 'error'); }
    });
  }

  async function stopPoller() {
    await withBtn('btn-stop', 'Stopping…', async () => {
      try {
        await api.stop();
        await refresh();
        showToast('Poller stopped');
      } catch (e) { showToast('Failed to stop', 'error'); }
    });
  }

  async function resetToday() {
    if (!confirm("Delete today's log?")) return;
    await withBtn('btn-reset', 'Deleting…', async () => {
      try {
        await api.reset_today();
        await refresh();
        showToast("Today's log cleared");
      } catch (e) { showToast('Failed to reset', 'error'); }
    });
  }

  async function deleteAll() {
    if (!confirm("Permanently delete all log files?\\nThis cannot be undone.")) return;
    await withBtn('btn-delete', 'Deleting…', async () => {
      try {
        await api.delete_all();
        await refresh();
        showToast('All logs deleted');
      } catch (e) { showToast('Failed to delete', 'error'); }
    });
  }

  // ── enrich & summarize ──────────────────────────────────────────────────────

  const TYPE_PILL = {
    coding:        'pill-coding',
    browser:       'pill-browser',
    meeting:       'pill-meeting',
    communication: 'pill-comm',
    design:        'pill-design',
    productivity:  'pill-productivity',
    git:           'pill-git',
    other:         'pill-other',
  };

  function typesPills(types) {
    if (!types) return '';
    return Object.entries(types)
      .sort((a, b) => b[1] - a[1])
      .map(([t, n]) => {
        const cls = TYPE_PILL[t] || 'pill-other';
        return `<span class="type-pill ${cls}">${t}&nbsp;${n}</span>`;
      }).join('');
  }

  function _esc(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  function _buildSummaryHtml(text) {
    const parts = [];
    for (const raw of text.split('\\n')) {
      const line = raw.trim();
      if (!line) continue;
      if (line.startsWith('## ')) {
        parts.push('<div class="summary-section-hdr">' + _esc(line.slice(3)) + '</div>');
        continue;
      }
      if (/^total:/i.test(line)) {
        const val = line.replace(/^total:\\s*/i, '');
        parts.push('<div class="summary-total"><span>Total</span><span>' + _esc(val) + '</span></div>');
        continue;
      }
      const m = line.match(/^(.+?)\\s+[(]([^)]+)[)](?:\\s+\\[([^\\]]*)\\])?$/);
      if (m) {
        const ref = m[3] ? '<span class="summary-ref">[' + _esc(m[3]) + ']</span>' : '';
        parts.push(
          '<div class="summary-row">' +
            '<span class="summary-desc">' + _esc(m[1]) + '</span>' +
            ref +
            '<span class="summary-dur">' + _esc(m[2]) + '</span>' +
          '</div>'
        );
        continue;
      }
      parts.push('<div class="summary-plain">' + _esc(line) + '</div>');
    }
    return parts.join('') || '<p class="no-logs">No sessions found.</p>';
  }

  function showOutput(text, ok, label) {
    const wrap = document.getElementById('summary-output');
    const lbl  = document.getElementById('result-label');
    const t    = text.trim();
    _lastOutput = t;
    if (lbl) lbl.textContent = t ? (label || '') : '';
    _promptVisible = false;
    document.getElementById('btn-preview-prompt')?.classList.remove('btn-active');

    if (!t) {
      wrap.innerHTML = '<p class="no-logs">Generate a summary to see it here…</p>';
      return;
    }
    if (!ok) {
      wrap.innerHTML = `<pre class="result-pre err" style="margin:0">${_esc(t)}</pre>`;
      return;
    }
    if (label && label.startsWith('Summary')) {
      wrap.innerHTML = _buildSummaryHtml(t);
    } else {
      wrap.innerHTML = `<pre class="result-pre" style="margin:0">${_esc(t)}</pre>`;
    }
  }

  async function runEnrich() {
    const date   = document.getElementById('action-date').value;
    const dryRun = document.getElementById('enrich-dry').checked;
    await withBtn('btn-enrich', 'Enriching…', async () => {
      try {
        const r = await api.enrich(date, dryRun);
        showOutput(r.output, r.ok, 'Git Enricher' + (dryRun ? ' — dry run' : ''));
        showToast(r.ok ? 'Enrichment done' : 'Enrichment failed', r.ok ? 'ok' : 'error');
      } catch (e) { showToast('Failed to run enricher', 'error'); }
    });
  }

  const _BACKEND_NAMES = { ollama: 'Ollama', council: 'Council', claude: 'Claude CLI', anthropic: 'Anthropic', openai: 'OpenAI' };

  function _setProgressLabel(text) {
    document.getElementById('progress-backend').textContent = text;
  }

  function showProgress(on, backendLabel) {
    const wrap    = document.getElementById('summary-progress');
    const elapsed = document.getElementById('progress-elapsed');
    const bLabel  = document.getElementById('progress-backend');
    if (on) {
      _progressStart = Date.now();
      bLabel.textContent  = 'Calling ' + (_BACKEND_NAMES[backendLabel] || 'LLM') + '…';
      elapsed.textContent = '0s';
      wrap.classList.add('visible');
      clearInterval(_progressTimer);
      _progressTimer = setInterval(() => {
        elapsed.textContent = Math.floor((Date.now() - _progressStart) / 1000) + 's';
      }, 1000);
    } else {
      clearInterval(_progressTimer);
      _progressTimer = null;
      wrap.classList.remove('visible');
    }
  }

  async function runSummary() {
    const date    = document.getElementById('action-date').value;
    const backend = document.getElementById('sum-backend').value;
    const bLabel  = backend || 'default';

    showProgress(true, backend);
    _setProgressLabel('Enriching git commits…');

    await withBtn('btn-summary', 'Generating…', async () => {
      try {
        await api.enrich(date, false);
        _setProgressLabel('Calling ' + (_BACKEND_NAMES[backend] || 'LLM') + '…');
        const r = await api.summarize(date, backend);
        showOutput(r.output, r.ok, 'Summary — ' + bLabel);
        showToast(r.ok ? 'Summary ready' : 'Summary failed', r.ok ? 'ok' : 'error');
      } catch (e) {
        showToast('Failed to generate summary', 'error');
      } finally {
        showProgress(false);
      }
    });
  }

  async function cancelSummary() {
    try { await api.cancel_summary(); } catch {}
    showProgress(false);
    showToast('Cancelled', 'ok');
  }

  async function previewPrompt() {
    const btn = document.getElementById('btn-preview-prompt');
    if (_promptVisible) {
      showOutput('', true, '');
      return;
    }
    const date = document.getElementById('action-date').value;
    await withBtn('btn-preview-prompt', '…', async () => {
      try {
        const r = await api.get_prompt_preview(date);
        showOutput(r.output, r.ok, 'Prompt Preview');
        if (r.ok) {
          _promptVisible = true;
          btn.classList.add('btn-active');
        }
      } catch (e) {
        showToast('Failed to get prompt', 'error');
      }
    });
  }

  async function copyOutput() {
    if (!_lastOutput) return;
    try {
      const r = await api.copy_to_clipboard(_lastOutput);
      showToast(r.ok ? 'Copied to clipboard' : 'Copy failed', r.ok ? 'ok' : 'error');
    } catch { showToast('Copy failed', 'error'); }
  }

  function quitApp() { api.quit(); }

  // ── log detail ───────────────────────────────────────────────────────────────

  function _delBtn(ts, date) {
    const safeTs   = ts.replace(/"/g, '&quot;');
    const safeDate = date.replace(/"/g, '&quot;');
    return `<button class="entry-del" title="Delete entry" onclick="deleteEntry('${safeDate}','${safeTs}')">×</button>`;
  }

  function renderDetailEntry(e, date) {
    const time = (e.ts || '').slice(11, 16);
    if (e.source === 'git') {
      const stats = `+${e.insertions||0}/-${e.deletions||0} in ${e.files_changed||0} file(s)`;
      return (
        '<div class="detail-entry">' +
          `<span class="detail-time">${time}</span>` +
          `<span class="type-pill pill-git" style="flex-shrink:0">git</span>` +
          '<span class="detail-text">' +
            `<div>${e.repo ? '[' + e.repo + '] ' : ''}${e.message || ''}</div>` +
            `<div class="detail-stats">${stats}</div>` +
          '</span>' +
          _delBtn(e.ts || '', date) +
        '</div>'
      );
    }
    const cls  = TYPE_PILL[e.type] || 'pill-other';
    const app  = e.app || '';
    const win  = e.tab_title || e.window || '';
    const url  = e.url || '';
    return (
      '<div class="detail-entry">' +
        `<span class="detail-time">${time}</span>` +
        `<span class="type-pill ${cls}" style="flex-shrink:0">${e.type||'other'}</span>` +
        '<span class="detail-text">' +
          `<div>${app}</div>` +
          (win ? `<div class="detail-sub">${win}</div>` : '') +
          (url ? `<div class="detail-sub" style="-webkit-user-select:text;user-select:text">${url}</div>` : '') +
        '</span>' +
        _delBtn(e.ts || '', date) +
      '</div>'
    );
  }

  async function deleteEntry(date, ts) {
    const r = await api.delete_entry(date, ts);
    if (!r.ok) { showToast('Failed to delete entry', 'error'); return; }
    await showLogDetail(date);
    showToast('Entry deleted');
  }

  async function showLogDetail(date) {
    clearInterval(_refreshTimer);
    _refreshTimer = null;
    setDate(date);
    const data = await api.log_entries(date);
    document.getElementById('detail-date').textContent = date;
    document.getElementById('detail-count').textContent =
      data.entries.length + (data.entries.length === 1 ? ' entry' : ' entries');
    const list = document.getElementById('detail-list');
    list.innerHTML = data.entries.length
      ? data.entries.map(e => renderDetailEntry(e, date)).join('')
      : '<p class="no-logs">No entries found.</p>';
    document.getElementById('view-main').style.display = 'none';
    document.getElementById('view-log-detail').style.display = 'flex';
  }

  function backFromDetail() {
    document.getElementById('view-log-detail').style.display = 'none';
    document.getElementById('view-main').style.display = 'flex';
    refresh();
    _refreshTimer = setInterval(refresh, 1000);
  }

  // ── init ────────────────────────────────────────────────────────────────────

  window.addEventListener('pywebviewready', async () => {
    api = window.pywebview.api;
    const hasConfig = await api.has_config();
    if (!hasConfig) { showSettings(true); } else { showMain(); }
  });
</script>
</body>
</html>
"""


# ── helpers ──────────────────────────────────────────────────────────────────

def _merge(stdout: str, stderr: str) -> str:
    """Combine stdout and stderr into one string shown in the UI."""
    parts = [s.strip() for s in (stdout, stderr) if s and s.strip()]
    return '\n\n'.join(parts)



# ── TOML helpers ─────────────────────────────────────────────────────────────

def _toml_str(s: str) -> str:
    return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"') + '"'


def _build_toml(d: dict) -> str:
    lines = [
        f'logs_dir = {_toml_str(d["logs_dir"])}',
        f'poll_interval = {int(d["poll_interval"])}',
        f'inactivity_timeout = {int(d["inactivity_timeout"])}',
        f'git_author = {_toml_str(d["git_author"])}',
    ]
    repos = d.get('git_repos') or []
    if repos:
        lines.append('git_repos = [')
        for r in repos:
            lines.append(f'    {_toml_str(r)},')
        lines.append(']')
    else:
        lines.append('git_repos = []')

    workspaces = d.get('git_workspaces') or []
    if workspaces:
        lines.append('git_workspaces = [')
        for w in workspaces:
            lines.append(f'    {_toml_str(w)},')
        lines.append(']')
    else:
        lines.append('git_workspaces = []')

    lines += [
        f'summarizer_backend = {_toml_str(d["summarizer_backend"])}',
        f'ollama_url = {_toml_str(d["ollama_url"])}',
        f'ollama_model = {_toml_str(d["ollama_model"])}',
        f'council_url = {_toml_str(d["council_url"])}',
        f'claude_model = {_toml_str(d["claude_model"])}',
        f'openai_api_key = {_toml_str(d["openai_api_key"])}',
        f'openai_model = {_toml_str(d["openai_model"])}',
    ]

    # [git_tags] must come AFTER all plain key=value lines — TOML tables
    # absorb every subsequent key until the next header.
    git_tags = d.get('git_tags') or {}
    if git_tags:
        lines.append('')
        lines.append('[git_tags]')
        for path, tags in git_tags.items():
            tags_str = '[' + ', '.join(_toml_str(t) for t in tags) + ']'
            lines.append(f'{_toml_str(path)} = {tags_str}')

    return '\n'.join(lines) + '\n'


# ── In-process poller thread ──────────────────────────────────────────────────

class _PollThread(threading.Thread):
    """Runs poll_once() in a background thread — no subprocess, full app permissions."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        from logger.activity_poller import poll_once, _build_tag_index
        log_dir = Path(Config.LOGS_DIR)
        tag_index = _build_tag_index()
        was_idle = False
        print('[worklog] poller thread started', flush=True)
        while not self._stop_event.is_set():
            idle = _system_idle_seconds() > Config.INACTIVITY_TIMEOUT
            if idle:
                if not was_idle:
                    was_idle = True
                    print('[worklog] idle — poller pausing', flush=True)
                self._stop_event.wait(10)
                continue
            if was_idle:
                was_idle = False
                print('[worklog] activity resumed', flush=True)
            try:
                poll_once(log_dir, tag_index)
            except Exception as e:
                print(f'[worklog] poll error: {e}', file=sys.stderr, flush=True)
            self._stop_event.wait(Config.POLL_INTERVAL)
        print('[worklog] poller thread stopped', flush=True)


# ── Python API exposed to JS ──────────────────────────────────────────────────

class _API:
    def __init__(self) -> None:
        self._poller: _PollThread | None = None
        self._manual_stop = False
        self._was_idle_stop = False
        self._cached_idle = 0.0
        self._poller_started_at: float = 0.0
        self._summary_proc: subprocess.Popen | None = None
        threading.Thread(target=self._auto_manager, daemon=True).start()
        if _CONFIG_PATH.exists():
            self._start_process()

    # ── config ────────────────────────────────────────────────────────────────

    def has_config(self) -> bool:
        return _CONFIG_PATH.exists()

    def get_settings(self) -> dict:
        paths = [
            {'path': r, 'workspace': False, 'tags': Config.GIT_REPO_TAGS.get(r, [])}
            for r in Config.GIT_REPOS
        ] + [
            {'path': w, 'workspace': True,  'tags': Config.GIT_REPO_TAGS.get(w, [])}
            for w in Config.GIT_WORKSPACES
        ]
        return {
            'logs_dir':             Config.LOGS_DIR,
            'poll_interval':        Config.POLL_INTERVAL,
            'inactivity_timeout':   Config.INACTIVITY_TIMEOUT,
            'git_author':           Config.GIT_AUTHOR,
            'git_paths':          paths,
            'summarizer_backend': Config.SUMMARIZER_BACKEND,
            'ollama_url':         Config.OLLAMA_URL,
            'ollama_model':       Config.OLLAMA_MODEL,
            'council_url':        Config.COUNCIL_URL,
            'claude_model':       Config.CLAUDE_MODEL,
            'openai_api_key':     Config.OPENAI_API_KEY,
            'openai_model':       Config.OPENAI_MODEL,
        }

    def save_settings(self, data: dict) -> dict:
        raw_paths = [p for p in (data.get('git_paths') or [])
                     if isinstance(p, dict) and str(p.get('path', '')).strip()]
        repos      = [p['path'] for p in raw_paths if not p.get('workspace')]
        workspaces = [p['path'] for p in raw_paths if p.get('workspace')]
        git_tags   = {p['path']: p['tags'] for p in raw_paths
                      if p.get('tags') and isinstance(p['tags'], list)}
        payload = {
            'logs_dir':             data.get('logs_dir', '~/.worklog/logs'),
            'poll_interval':        int(data.get('poll_interval', 300)),
            'inactivity_timeout':   int(data.get('inactivity_timeout', 300)),
            'git_author':           data.get('git_author', ''),
            'git_repos':          repos,
            'git_workspaces':     workspaces,
            'git_tags':           git_tags,
            'summarizer_backend': data.get('summarizer_backend', 'ollama'),
            'ollama_url':         data.get('ollama_url', 'http://localhost:11434'),
            'ollama_model':       data.get('ollama_model', 'qwen2.5:7b'),
            'council_url':        data.get('council_url', 'http://localhost:8001'),
            'claude_model':       data.get('claude_model', ''),
            'openai_api_key':     data.get('openai_api_key', ''),
            'openai_model':       data.get('openai_model', 'gpt-4o-mini'),
        }
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_PATH.write_text(_build_toml(payload))

        Config.LOGS_DIR             = str(Path(payload['logs_dir']).expanduser())
        Config.POLL_INTERVAL        = payload['poll_interval']
        Config.INACTIVITY_TIMEOUT   = payload['inactivity_timeout']
        Config.GIT_AUTHOR         = payload['git_author']
        Config.GIT_REPOS          = repos
        Config.GIT_WORKSPACES     = workspaces
        Config.GIT_REPO_TAGS      = git_tags
        Config.SUMMARIZER_BACKEND = payload['summarizer_backend']
        Config.OLLAMA_URL         = payload['ollama_url']
        Config.OLLAMA_MODEL       = payload['ollama_model']
        Config.COUNCIL_URL        = payload['council_url']
        Config.CLAUDE_MODEL       = payload['claude_model']
        Config.OPENAI_API_KEY     = payload['openai_api_key']
        Config.OPENAI_MODEL       = payload['openai_model']

        return {"ok": True}

    # ── auto-manager ──────────────────────────────────────────────────────────

    def _auto_manager(self) -> None:
        """Background thread: auto-stop on inactivity, auto-restart on activity."""
        _was_idle = False
        while True:
            try:
                time.sleep(10)
                timeout = max(60, Config.INACTIVITY_TIMEOUT)
                idle = _system_idle_seconds()
                self._cached_idle = idle
                running = self._poller is not None and self._poller.is_alive()
                # Grace period: don't stop a poller that was just started
                uptime = time.time() - self._poller_started_at
                if idle > timeout and uptime >= timeout:
                    if running:
                        _was_idle = True
                        self._was_idle_stop = True
                        self._stop_process()
                elif _was_idle and not self._manual_stop:
                    try:
                        self._start_process()
                        _was_idle = False
                        self._was_idle_stop = False
                    except Exception:
                        pass  # retry on next tick; UI stays "Paused"
            except Exception:
                pass  # never let an exception kill this thread

    # ── poller control ────────────────────────────────────────────────────────

    @staticmethod
    def _subprocess_env() -> dict:
        """Build subprocess environment: UTF-8 I/O + bundled module path when frozen."""
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        env.setdefault('LANG', 'en_US.UTF-8')
        env.setdefault('LC_ALL', 'en_US.UTF-8')
        # Augment PATH with common user-level install dirs that macOS app bundles omit.
        extra = [
            str(Path.home() / '.local' / 'bin'),
            '/opt/homebrew/bin',
            '/usr/local/bin',
        ]
        current_path = env.get('PATH', '')
        additions = [p for p in extra if p not in current_path.split(':')]
        if additions:
            env['PATH'] = ':'.join(additions) + (':' + current_path if current_path else '')
        if getattr(sys, 'frozen', False):
            env['PYTHONPATH'] = ':'.join(p for p in sys.path if p)
        return env

    def _start_process(self) -> None:
        if self._poller is None or not self._poller.is_alive():
            self._poller = _PollThread()
            self._poller.start()
            self._poller_started_at = time.time()

    def _stop_process(self) -> None:
        if self._poller and self._poller.is_alive():
            self._poller.stop()
            self._poller = None

    def start(self) -> dict:
        self._manual_stop = False
        self._was_idle_stop = False
        self._start_process()
        return {"ok": True}

    def stop(self) -> dict:
        self._manual_stop = True
        self._was_idle_stop = False
        self._stop_process()
        return {"ok": True}

    def quit(self) -> None:
        self._stop_process()
        os._exit(0)

    # ── status + stats ────────────────────────────────────────────────────────

    def status(self) -> dict:
        running = self._poller is not None and self._poller.is_alive()
        last_ts, count = self._today_stats()
        return {
            "running": running,
            "last_ts": last_ts,
            "count": count,
            "idle_seconds": round(self._cached_idle),
            "inactivity_timeout": Config.INACTIVITY_TIMEOUT,
            "auto_paused": self._was_idle_stop,
        }

    # ── log list ──────────────────────────────────────────────────────────────

    def logs(self) -> dict:
        log_dir = Path(Config.LOGS_DIR)
        today = datetime.now().strftime("%Y-%m-%d")
        files = sorted(log_dir.glob("*.jsonl"), reverse=True) if log_dir.exists() else []
        result = []
        for f in files:
            types: dict[str, int] = {}
            git_count = 0
            first_ts = ''
            last_ts = ''
            count = 0
            try:
                for ln in f.read_text().splitlines():
                    if not ln.strip():
                        continue
                    try:
                        e = json.loads(ln)
                    except json.JSONDecodeError:
                        continue
                    count += 1
                    if e.get('source') == 'git':
                        git_count += 1
                        types['git'] = types.get('git', 0) + 1
                    else:
                        t = e.get('type', 'other')
                        types[t] = types.get(t, 0) + 1
                    ts = e.get('ts', '')
                    if ts:
                        if not first_ts or ts < first_ts:
                            first_ts = ts
                        if not last_ts or ts > last_ts:
                            last_ts = ts
            except OSError:
                pass
            result.append({
                "date": f.stem,
                "count": count,
                "today": f.stem == today,
                "types": types,
                "git_count": git_count,
                "first_ts": first_ts[11:16] if first_ts else '',
                "last_ts": last_ts[11:16] if last_ts else '',
            })
        return {"logs": result}

    # ── log actions ───────────────────────────────────────────────────────────

    def log_entries(self, date: str) -> dict:
        f = Path(Config.LOGS_DIR) / f"{date}.jsonl"
        entries = []
        if f.exists():
            try:
                for ln in f.read_text().splitlines():
                    if ln.strip():
                        try:
                            entries.append(json.loads(ln))
                        except json.JSONDecodeError:
                            pass
            except OSError:
                pass
        entries.sort(key=lambda e: e.get('ts', ''))
        return {"date": date, "entries": entries}

    def delete_entry(self, date: str, ts: str) -> dict:
        f = Path(Config.LOGS_DIR) / f"{date}.jsonl"
        if not f.exists():
            return {"ok": False}
        try:
            lines = f.read_text().splitlines()
            kept = []
            removed = False
            for line in lines:
                if not line.strip():
                    continue
                if not removed:
                    try:
                        entry = json.loads(line)
                        if entry.get('ts') == ts:
                            removed = True
                            continue
                    except json.JSONDecodeError:
                        pass
                kept.append(line)
            f.write_text('\n'.join(kept) + ('\n' if kept else ''))
            return {"ok": removed}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

    def reset_today(self) -> dict:
        f = Path(Config.LOGS_DIR) / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        f.unlink(missing_ok=True)
        return {"ok": True}

    def delete_all(self) -> dict:
        log_dir = Path(Config.LOGS_DIR)
        if log_dir.exists():
            for f in log_dir.glob("*.jsonl"):
                f.unlink(missing_ok=True)
        return {"ok": True}

    # ── enrich + summarize ────────────────────────────────────────────────────

    def enrich(self, date: str = '', dry_run: bool = False) -> dict:
        cmd = [sys.executable, "-m", "logger.git_enricher"]
        if date:
            cmd += ["--date", date]
        if dry_run:
            cmd.append("--dry-run")
        r = subprocess.run(cmd, cwd=str(_ROOT), capture_output=True, text=True,
                           encoding='utf-8', timeout=60, env=self._subprocess_env())
        output = _merge(r.stdout, r.stderr)
        return {"ok": r.returncode == 0, "output": output}

    def copy_to_clipboard(self, text: str) -> dict:
        r = subprocess.run(['pbcopy'], input=text, text=True, encoding='utf-8')
        return {"ok": r.returncode == 0}

    def get_prompt_preview(self, date: str = '') -> dict:
        cmd = [sys.executable, '-m', 'summarizer.daily_summary', '--print-prompt']
        if date:
            cmd += ['--date', date]
        try:
            r = subprocess.run(cmd, cwd=str(_ROOT), capture_output=True, text=True,
                               encoding='utf-8', timeout=30, env=self._subprocess_env())
            output = _merge(r.stdout, r.stderr)
            return {'ok': r.returncode == 0, 'output': output}
        except subprocess.TimeoutExpired:
            return {'ok': False, 'output': 'ERROR: timed out'}
        except Exception as exc:
            return {'ok': False, 'output': f'ERROR: {exc}'}

    def summarize(self, date: str = '', backend: str = '') -> dict:
        cmd = [sys.executable, "-m", "summarizer.daily_summary"]
        if date:
            cmd += ["--date", date]
        if backend:
            cmd += ["--backend", backend]
        try:
            self._summary_proc = subprocess.Popen(
                cmd, cwd=str(_ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', env=self._subprocess_env(),
            )
            try:
                stdout, stderr = self._summary_proc.communicate(timeout=300)
            except subprocess.TimeoutExpired:
                self._summary_proc.kill()
                self._summary_proc.communicate()
                return {"ok": False, "output": "ERROR: summarizer timed out after 5 minutes"}
            rc = self._summary_proc.returncode
            if rc == 0:
                # Prefer the clean saved summary over the noisy subprocess log.
                saved = self.get_summary(date)
                if saved.get("exists"):
                    return {"ok": True, "output": saved["output"],
                            "saved_at": saved["saved_at"]}
            return {"ok": rc == 0, "output": _merge(stdout, stderr)}
        except Exception as exc:
            return {"ok": False, "output": f"ERROR: {exc}"}
        finally:
            self._summary_proc = None

    def get_summary(self, date: str = '') -> dict:
        """Return the saved summary for a date, if one exists on disk."""
        d = date or datetime.now().strftime('%Y-%m-%d')
        path = Path(Config.SUMMARIES_DIR) / f'{d}.md'
        if not path.exists():
            return {"ok": True, "exists": False, "output": "", "saved_at": ""}
        try:
            text = path.read_text(encoding='utf-8')
            saved_at = datetime.fromtimestamp(path.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
            return {"ok": True, "exists": True, "output": text, "saved_at": saved_at}
        except OSError as exc:
            return {"ok": False, "exists": False, "output": f"ERROR: {exc}", "saved_at": ""}

    def cancel_summary(self) -> dict:
        proc = self._summary_proc
        if proc and proc.poll() is None:
            proc.kill()
            return {"ok": True}
        return {"ok": False}

    # ── helpers ───────────────────────────────────────────────────────────────

    def _today_stats(self) -> tuple[str, int]:
        f = Path(Config.LOGS_DIR) / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        if not f.exists():
            return "", 0
        try:
            lines = [ln for ln in f.read_text().splitlines() if ln.strip()]
            for line in reversed(lines):
                try:
                    entry = json.loads(line)
                    if "ts" in entry:
                        return entry["ts"], len(lines)
                except json.JSONDecodeError:
                    continue
            return "", len(lines)
        except OSError:
            return "", 0


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    api = _API()
    window = webview.create_window(
        "worklog", html=_HTML, js_api=api,
        width=560, height=820, min_size=(400, 560), resizable=True,
    )
    # Event handlers must return None (pywebview uses a set for return values;
    # returning a dict raises unhashable type: 'dict')
    window.events.closed += lambda: api._stop_process()
    webview.start()


if __name__ == "__main__":
    main()
