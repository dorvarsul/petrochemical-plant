# Phase 2.5 — bring-up checklist

Superseded by **[`DEPLOY.md`](../docs/DEPLOY.md)** in `docs/`, which has the
exact step-by-step commands (kept in one file so it can't drift out of sync
with this one). Use that file for the real bring-up; this one stays only as
a short pre-flight checklist to tick off before you start:

- [ ] All four PCs wired (not Wi-Fi — less jitter) into one **L3 (routed)
      switch**, each layer in its **own VLAN/subnet**.
- [ ] L3 switch configured: inter-VLAN routing on, and 502/8081/8082/8092
      permitted between PC1/PC4's subnets and the PC2/PC3 (PLC) subnets.
      Verify with `ip route get <PLC-IP>` and `nc -zv <PLC-IP> 502` from PC1.
- [ ] NTP enabled on all four (`timedatectl` shows synchronized) — the
      cross-machine time-sync risk in `ROADMAP.md` is real; don't skip this.
- [ ] `deploy/hosts.env` created from `hosts.env.example`, filled in (IPs in
      each layer's subnet), and copied identically to all four PCs.
- [ ] PC2/PC3 have Docker + the Compose plugin; PC1/PC4 have Python 3.10+.

Then follow `DEPLOY.md` from step 1.
