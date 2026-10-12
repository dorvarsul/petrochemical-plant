# Phase 2.5 — bring-up checklist

Superseded by **[`DEPLOY.md`](../DEPLOY.md)** at the repo root, which has the
exact step-by-step commands (kept in one file so it can't drift out of sync
with this one). Use that file for the real bring-up; this one stays only as
a short pre-flight checklist to tick off before you start:

- [ ] All four PCs on the same L2 switch, wired (not Wi-Fi — less jitter).
- [ ] NTP enabled on all four (`timedatectl` shows synchronized) — the
      cross-machine time-sync risk in `ROADMAP.md` is real; don't skip this.
- [ ] `deploy/hosts.env` created from `hosts.env.example`, filled in, and
      copied identically to all four PCs.
- [ ] PC2/PC3 have Docker + the Compose plugin; PC1/PC4 have Python 3.10+.

Then follow `DEPLOY.md` from step 1.
