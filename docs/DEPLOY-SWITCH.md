# PetroTwin — L3 switch setup (Cisco Catalyst 9200-24) + ACLs

How to cable the four Phase-2.5 PCs (plus an optional attacker box) into one
**Cisco Catalyst 9200-24** (C9200-24T/P, or the C9200L variants; IOS XE), give
each layer its **own VLAN/subnet**, route between them on the switch, and lock
the routed hops down with **ACLs** so only the traffic the testbed actually
uses gets through.

This file covers only the switch and the PCs' network settings. Do it **before
[`DEPLOY.md`](DEPLOY.md) §5** (the reachability check). Everything else
(`hosts.env`, containers, launch order) stays in `DEPLOY.md`.

> **Keep the switch isolated.** This is an attack testbed. Don't uplink, trunk
> or patch this switch into a building, corporate or plant network, and leave
> the `Gi0/0` management port unplugged. None of the config below needs any
> outside connectivity.

> **Status:** these are standard IOS XE commands, but they haven't been run on
> the lab's switch yet, same as the rest of the physical 4-PC run (see
> `deploy/README.md` "Status"). Do the verification steps in §7 and §8.4; don't
> assume the config works just because the paste went through.

## 1. Addressing plan

There is one VLAN per layer, one /24 per VLAN, and the switch's SVI is `.1` in
each subnet. PC2's and PC3's addresses match `deploy/hosts.env.example`. If
you pick different addresses, change them **here and in `hosts.env`
together**, because the ACLs in §8 name each host exactly.

| Host | Role | Switch port | VLAN | Subnet | Host IP | Gateway (SVI) |
|------|------|-------------|------|--------|---------|---------------|
| PC1 | physics + 3D + Modbus bridge | `Gi1/0/1` | 10 `PT-PHYSICS` | 10.0.1.0/24 | 10.0.1.11 | 10.0.1.1 |
| PC2 | Control PLC (BPCS) | `Gi1/0/2` | 20 `PT-BPCS` | 10.0.2.0/24 | 10.0.2.12 | 10.0.2.1 |
| PC3 | Safety PLC (SIS), the attack target | `Gi1/0/3` | 30 `PT-SIS` | 10.0.3.0/24 | 10.0.3.13 | 10.0.3.1 |
| PC4 | standalone operator HMI | `Gi1/0/4` | 40 `PT-HMI` | 10.0.4.0/24 | 10.0.4.14 | 10.0.4.1 |
| ATK | attacker box (optional, 5th PC) | `Gi1/0/5` | 50 `PT-ATTACKER` | 10.0.5.0/24 | 10.0.5.15 | 10.0.5.1 |
| — | unused ports (shut down) | `Gi1/0/6`–`24` | 999 `PT-UNUSED` | — | — | — |

```
                    Cisco Catalyst 9200-24  (hostname PT-SW1)
           inter-VLAN routing on SVIs, one inbound ACL per SVI (§8)
  ┌──────────────┬──────────────┬──────────────┬──────────────┬──────────────┐
  │ Gi1/0/1      │ Gi1/0/2      │ Gi1/0/3      │ Gi1/0/4      │ Gi1/0/5      │
  │ VLAN 10      │ VLAN 20      │ VLAN 30      │ VLAN 40      │ VLAN 50      │
  │ 10.0.1.1/24  │ 10.0.2.1/24  │ 10.0.3.1/24  │ 10.0.4.1/24  │ 10.0.5.1/24  │
  └──────┬───────┴──────┬───────┴──────┬───────┴──────┬───────┴──────┬───────┘
         │              │              │              │              │
     PC1 .1.11      PC2 .2.12      PC3 .3.13      PC4 .4.14      ATK .5.15
     physics+3D     BPCS           SIS (target)   HMI            attacker
     + bridge       OpenPLC        OpenPLC        Modbus master  (optional)
```

All five subnets are directly connected to the switch, so you don't need static
routes or a routing protocol. Turning on `ip routing` is enough. If you have no
fifth PC, leave VLAN 50 empty and run the attack from PC4, which the ACLs
already let reach both PLCs (§9 explains why).

## 2. Cabling

1. Use straight-through Cat5e/Cat6 patch cables. The switch auto-detects
   MDI/MDI-X, so crossover cables aren't needed.
2. Plug each PC's **wired** NIC into the port in the table: PC1 → `Gi1/0/1`,
   PC2 → `Gi1/0/2`, PC3 → `Gi1/0/3`, PC4 → `Gi1/0/4`, attacker → `Gi1/0/5`.
   Label both ends of each cable, because a cable in the wrong port puts that
   PC in the wrong VLAN and the ACLs will then block it.
3. Don't use Wi-Fi for testbed traffic (it adds jitter). A PC can keep Wi-Fi up
   for its own internet access. §6 only routes `10.0.0.0/16` through the switch,
   so the Wi-Fi default route stays in place.
4. Configure the switch through its **console port**. Both kinds of console
   connection run at 9600 baud, 8N1:
   - **RJ-45 console:** use a blue Cisco rollover cable and a USB-serial
     adapter. It shows up on Linux as `/dev/ttyUSB0`.
   - **USB mini-B console:** connect it directly. It shows up as `/dev/ttyACM0`.

   From any of the Ubuntu PCs:
   ```bash
   sudo apt install screen          # offline: add it to the USB kit
   sudo screen /dev/ttyUSB0 9600    # or /dev/ttyACM0; quit with Ctrl-A then K
   ```

## 3. First login

Power on the switch and wait a few minutes for it to boot. Press Enter to get
a prompt. A factory-fresh switch may ask some setup questions first:

- `Would you like to terminate autoinstall? [yes]` → **yes**
- `Would you like to enter the initial configuration dialog? [yes/no]` → **no**

Then you'll be at `Switch>`. If the switch was used before, check what's on it
before you overwrite anything: `enable` → `show running-config`. To start from a
clean switch, see §10.

## 4. Base config: VLANs, gateways, access ports

Paste this block into the console. Everything after `!` on a line is a comment,
and IOS accepts it as pasted.

```
enable
configure terminal
hostname PT-SW1
enable secret <pick-a-password>
no ip domain lookup          ! a mistyped command won't stall on a DNS lookup
ip routing                   ! inter-VLAN routing (harmless if already on)
!
vlan 10
 name PT-PHYSICS
vlan 20
 name PT-BPCS
vlan 30
 name PT-SIS
vlan 40
 name PT-HMI
vlan 50
 name PT-ATTACKER
vlan 999
 name PT-UNUSED
exit
!
! --- one gateway (SVI) per layer --------------------------------------------
interface Vlan1
 no ip address
 shutdown
interface Vlan10
 description GW PC1 physics+3D+bridge
 ip address 10.0.1.1 255.255.255.0
 no shutdown
interface Vlan20
 description GW PC2 Control PLC (BPCS)
 ip address 10.0.2.1 255.255.255.0
 no shutdown
interface Vlan30
 description GW PC3 Safety PLC (SIS)
 ip address 10.0.3.1 255.255.255.0
 no shutdown
interface Vlan40
 description GW PC4 operator HMI
 ip address 10.0.4.1 255.255.255.0
 no shutdown
interface Vlan50
 description GW attacker (optional)
 ip address 10.0.5.1 255.255.255.0
 no shutdown
!
! --- one access port per PC -------------------------------------------------
interface GigabitEthernet1/0/1
 description PC1 physics+3D+bridge
 switchport mode access
 switchport access vlan 10
 spanning-tree portfast
 no shutdown
interface GigabitEthernet1/0/2
 description PC2 Control PLC (BPCS)
 switchport mode access
 switchport access vlan 20
 spanning-tree portfast
 no shutdown
interface GigabitEthernet1/0/3
 description PC3 Safety PLC (SIS)
 switchport mode access
 switchport access vlan 30
 spanning-tree portfast
 no shutdown
interface GigabitEthernet1/0/4
 description PC4 operator HMI
 switchport mode access
 switchport access vlan 40
 spanning-tree portfast
 no shutdown
interface GigabitEthernet1/0/5
 description ATK attacker (optional)
 switchport mode access
 switchport access vlan 50
 spanning-tree portfast
 no shutdown
!
! --- park and disable everything else ---------------------------------------
interface range GigabitEthernet1/0/6 - 24
 description unused
 switchport mode access
 switchport access vlan 999
 shutdown
end
```

`spanning-tree portfast` lets a PC's port pass traffic as soon as the link comes
up, instead of waiting about 30 seconds. Expect a warning about it. Only use it
on ports that connect to an end host.

If your model has uplink ports (`Gi1/1/1`–`4` or `Te1/1/1`–`4`; check
`show ip interface brief`), shut them down too:
`interface range GigabitEthernet1/1/1 - 4` → `shutdown`.

## 5. The switch as the lab's NTP server

The checklist requires NTP on all four PCs, and the testbed has no internet
connection. The switch is the one device every PC can reach, so make it the
time source ([`DEPLOY-OFFLINE.md`](DEPLOY-OFFLINE.md) §A5, "local NTP source").

From privileged EXEC (`PT-SW1#`), set the switch clock to the current **UTC**
time. Run `date -u` on any PC to get it.

```
clock set 09:00:00 13 Oct 2026
configure terminal
ntp master 3                 ! serve NTP from the local clock, stratum 3
end
```

The PCs only need to agree with each other, not with real-world time, so a
clock that's a few seconds off doesn't matter. On each PC, set its **own
gateway** as the NTP server:

- **systemd-timesyncd** (the default on Ubuntu 24.04 and earlier): in
  `/etc/systemd/timesyncd.conf`, under `[Time]`, set `NTP=10.0.1.1` (PC1), or
  `.2.1`, `.3.1`, `.4.1`, `.5.1` on the others. Then:
  ```bash
  sudo systemctl restart systemd-timesyncd
  timedatectl timesync-status        # "Server: 10.0.N.1" + a recent Packet count
  ```
- **chrony**: `server 10.0.N.1 iburst` in `/etc/chrony/chrony.conf`, then
  `sudo systemctl restart chrony` and `chronyc sources` (look for a `*`).

## 6. Static IPs on the PCs

Do this on each PC, using that PC's row from §1. Only `10.0.0.0/16` is routed
through the switch, so the PC keeps any other default route (such as Wi-Fi).

**Ubuntu Desktop (NetworkManager):**

```bash
ip -br link                                   # find the wired NIC, e.g. enp3s0
sudo nmcli con mod "Wired connection 1" connection.autoconnect no 2>/dev/null
sudo nmcli con add type ethernet ifname enp3s0 con-name petrotwin \
     ipv4.method manual ipv4.addresses 10.0.1.11/24 \
     ipv4.routes "10.0.0.0/16 10.0.1.1" ipv4.never-default yes \
     ipv6.method disabled
sudo nmcli con up petrotwin
```

On each PC, replace `10.0.1.11/24` and `10.0.1.1` with that PC's host IP and
gateway: PC2 `10.0.2.12` / `10.0.2.1`, PC3 `10.0.3.13` / `10.0.3.1`, PC4
`10.0.4.14` / `10.0.4.1`, ATK `10.0.5.15` / `10.0.5.1`.

**Ubuntu Server (netplan + networkd):** create `/etc/netplan/60-petrotwin.yaml`
and remove any other netplan entry for the same NIC:

```yaml
network:
  version: 2
  ethernets:
    enp3s0:
      dhcp4: false
      addresses: [10.0.1.11/24]
      routes:
        - to: 10.0.0.0/16
          via: 10.0.1.1
```

```bash
sudo chmod 600 /etc/netplan/60-petrotwin.yaml && sudo netplan apply
```

## 7. Verify routing, before any ACLs

Get plain routing working before adding the ACLs. That way, if something fails
later, you know the cause is an ACL and not the cabling or addressing.

On the switch:

```
show vlan brief                  ! Gi1/0/1..5 listed under VLANs 10..50
show interfaces status           ! Gi1/0/1..5 "connected", right VLAN
show ip interface brief          ! Vlan10..Vlan50 up/up (Vlan50 only if ATK is plugged in)
show ip route connected          ! five "C" routes, 10.0.1.0/24 .. 10.0.5.0/24
show ip arp                      ! each PC's IP once it has sent anything
```

On the PCs (example from PC1):

```bash
ip route get 10.0.3.13           # must say "via 10.0.1.1 dev enp3s0"
ping -c3 10.0.1.1                # own gateway
ping -c3 10.0.2.12               # PC2, routed across the switch
ping -c3 10.0.3.13               # PC3
ping -c3 10.0.4.14               # PC4
```

An SVI stays **down** until at least one port in its VLAN is up. So if
`Vlan30` is down, the problem is PC3's link, not the SVI.

## 8. ACLs

### 8.1 What has to cross the switch

This table comes from the code. The ACLs in §8.2 permit these flows and deny
everything else. The ports are the **host** ports from `common/deploy_config.py`
(Docker maps the PLCs' internal `502`/`8080` to these), so the switch never sees
TCP 502.

| From | To | TCP port | Why (in the code) |
|------|----|----------|-------------------|
| PC1 | PC2 | 5020 | the bridge's Modbus master → BPCS (`bridge/plc_io.py`) |
| PC1 | PC2 | 8081 | OpenPLC web UI: `load_plant` / `restart_runtime` (`ui/plant_server.py`) |
| PC1 | PC3 | 5021 | the bridge → SIS |
| PC1 | PC3 | 8082 | load/restart the SIS program |
| PC4 | PC2 | 5020 | HMI polling + operator writes: EXPORT valve, the HMI's own demo attack/stand-down buttons (`ui/hmi_server.py`) |
| PC4 | PC2 | 8081 | HMI reset → `restart_runtime` |
| PC4 | PC3 | 5021 | HMI polling + operator writes: FILL valve, SIS reset pushbutton, demo bypass |
| PC4 | PC3 | 8082 | HMI reset → `restart_runtime` |
| PC4 | PC1 | 8092 | *(optional)* view the 3D ground truth from the HMI station |
| PC1 | PC4 | 8093 | *(optional)* view the HMI from PC1 |
| ATK | PC2 | 5020 | **the attack**: BPCS MANUAL mode + drive output (`attack/attack.py`) |
| ATK | PC3 | 5021 | **the attack**: SIS maintenance bypass |
| every host | its own gateway | UDP 123 | NTP from the switch (§5) |
| testbed | testbed | ICMP | ping for bring-up and troubleshooting |

Flows that are deliberately **not** permitted:
- The PLCs never start a connection, including to each other, because the BPCS
  and the SIS are independent.
- The attacker gets Modbus only. It can't reach the PLC web UIs, PC1 or PC4.
- Nothing can reach SSH or other ports on any PC.

If you change a port through a `PETROTWIN_*_PORT` variable, change the matching
ACL line too.

### 8.2 The ACL config

There is one named extended ACL per VLAN, applied **inbound** on that VLAN's
SVI. Each ACL controls what that layer is allowed to send into the routed core.
Router ACLs are stateless, so the PLC VLANs need explicit lines for their
replies. The `established` keyword matches only TCP packets with ACK or RST set,
which means a PLC can **answer** a connection but can't **open** one. Each VLAN
holds a single PC, so there's no traffic inside a VLAN that would bypass these
ACLs.

```
configure terminal
!
! ===== VLAN 10 — PC1 physics + 3D + bridge ==================================
ip access-list extended PT-PHYSICS-IN
 remark bridge + program load -> both PLCs
 permit tcp host 10.0.1.11 host 10.0.2.12 eq 5020
 permit tcp host 10.0.1.11 host 10.0.2.12 eq 8081
 permit tcp host 10.0.1.11 host 10.0.3.13 eq 5021
 permit tcp host 10.0.1.11 host 10.0.3.13 eq 8082
 remark (optional) PC1 browser -> PC4 HMI page, and 3D page replies -> PC4
 permit tcp host 10.0.1.11 host 10.0.4.14 eq 8093
 permit tcp host 10.0.1.11 eq 8092 host 10.0.4.14 established
 remark infra: NTP to own gateway, ping
 permit udp host 10.0.1.11 host 10.0.1.1 eq 123
 permit icmp 10.0.1.0 0.0.0.255 10.0.0.0 0.0.255.255
 deny   ip any any log
!
! ===== VLAN 20 — PC2 Control PLC (BPCS): answers only, never initiates ======
ip access-list extended PT-BPCS-IN
 remark replies to the bridge (PC1)
 permit tcp host 10.0.2.12 eq 5020 host 10.0.1.11 established
 permit tcp host 10.0.2.12 eq 8081 host 10.0.1.11 established
 remark replies to the HMI (PC4)
 permit tcp host 10.0.2.12 eq 5020 host 10.0.4.14 established
 permit tcp host 10.0.2.12 eq 8081 host 10.0.4.14 established
 remark replies to the attacker (Modbus only)
 permit tcp host 10.0.2.12 eq 5020 host 10.0.5.15 established
 remark infra
 permit udp host 10.0.2.12 host 10.0.2.1 eq 123
 permit icmp 10.0.2.0 0.0.0.255 10.0.0.0 0.0.255.255
 deny   ip any any log
!
! ===== VLAN 30 — PC3 Safety PLC (SIS): answers only, never initiates =======
ip access-list extended PT-SIS-IN
 remark replies to the bridge (PC1)
 permit tcp host 10.0.3.13 eq 5021 host 10.0.1.11 established
 permit tcp host 10.0.3.13 eq 8082 host 10.0.1.11 established
 remark replies to the HMI (PC4)
 permit tcp host 10.0.3.13 eq 5021 host 10.0.4.14 established
 permit tcp host 10.0.3.13 eq 8082 host 10.0.4.14 established
 remark replies to the attacker (Modbus only)
 permit tcp host 10.0.3.13 eq 5021 host 10.0.5.15 established
 remark infra
 permit udp host 10.0.3.13 host 10.0.3.1 eq 123
 permit icmp 10.0.3.0 0.0.0.255 10.0.0.0 0.0.255.255
 deny   ip any any log
!
! ===== VLAN 40 — PC4 operator HMI ===========================================
ip access-list extended PT-HMI-IN
 remark Modbus polling/writes + reset (web) -> both PLCs
 permit tcp host 10.0.4.14 host 10.0.2.12 eq 5020
 permit tcp host 10.0.4.14 host 10.0.2.12 eq 8081
 permit tcp host 10.0.4.14 host 10.0.3.13 eq 5021
 permit tcp host 10.0.4.14 host 10.0.3.13 eq 8082
 remark (optional) PC4 browser -> PC1 3D page, and HMI page replies -> PC1
 permit tcp host 10.0.4.14 host 10.0.1.11 eq 8092
 permit tcp host 10.0.4.14 eq 8093 host 10.0.1.11 established
 remark infra
 permit udp host 10.0.4.14 host 10.0.4.1 eq 123
 permit icmp 10.0.4.0 0.0.0.255 10.0.0.0 0.0.255.255
 deny   ip any any log
!
! ===== VLAN 50 — attacker (DEMO profile: the attack path is open) ===========
ip access-list extended PT-ATTACKER-IN
 10 permit tcp host 10.0.5.15 host 10.0.2.12 eq 5020
 20 permit tcp host 10.0.5.15 host 10.0.3.13 eq 5021
 30 permit udp host 10.0.5.15 host 10.0.5.1 eq 123
 40 permit icmp 10.0.5.0 0.0.0.255 10.0.0.0 0.0.255.255
 50 deny   ip any any log
!
end
```

The attacker's lines have explicit sequence numbers so that §9 can toggle the
attack path with `no 10` / `no 20`. In the other ACLs, IOS numbers the lines
10, 20, 30… automatically.

### 8.3 Apply them

```
configure terminal
interface Vlan10
 ip access-group PT-PHYSICS-IN in
interface Vlan20
 ip access-group PT-BPCS-IN in
interface Vlan30
 ip access-group PT-SIS-IN in
interface Vlan40
 ip access-group PT-HMI-IN in
interface Vlan50
 ip access-group PT-ATTACKER-IN in
end
copy running-config startup-config
```

To edit an ACL later, the simplest way is `no ip access-list extended <NAME>`
followed by pasting the whole ACL again. IOS treats an applied ACL that doesn't
exist as permit-all, so the SVI stays reachable for the moment in between.

### 8.4 Verify

On the switch:

```
show access-lists                         ! per-line match counters
show ip interface Vlan30 | include access list
                                          ! "Inbound  access list is PT-SIS-IN"
show logging | include IPACCESSLOG        ! the "deny ... log" hits (rate-limited)
```

On the PCs, the **allowed** paths should connect. These are the same checks as
[`DEPLOY.md`](DEPLOY.md) §5:

```bash
# from PC1 and from PC4:
nc -zv -w2 10.0.2.12 5020 ; nc -zv -w2 10.0.2.12 8081
nc -zv -w2 10.0.3.13 5021 ; nc -zv -w2 10.0.3.13 8082
# from ATK:
nc -zv -w2 10.0.2.12 5020 ; nc -zv -w2 10.0.3.13 5021
```

These **blocked** paths should time out or fail with `No route to host`:

```bash
# PC2 -> PC3: the PLCs never initiate, not even to each other
nc -zv -w2 10.0.3.13 5021
# ATK -> PLC web UI, ATK -> PC1: the attacker only gets Modbus
nc -zv -w2 10.0.3.13 8082 ; nc -zv -w2 10.0.1.11 8092
# PC4 -> PC1 SSH: no management paths between the PCs
nc -zv -w2 10.0.1.11 22
```

Here's how to read the `nc` result:
- `succeeded` means the path is open and something is listening.
- `Connection refused` means the switch let the connection through but nothing
  is listening on the target port. The ACL is fine; the service isn't running
  (check `pc2.sh status` / `pc3.sh status`).
- A timeout or `No route to host` means the connection was blocked, by an ACL
  or by the far PC's host firewall.

## 9. Optional: the segmentation contrast

The ACLs in §8 are the **demo profile**. They deliberately leave the attack
path open, because the whole point of the testbed is that the SIS can be
reached over Modbus. To show what switch ACLs can and can't do, run the attack
twice.

**1. Close the attacker VLAN's path:**

```
configure terminal
ip access-list extended PT-ATTACKER-IN
 no 10
 no 20
end
```

Now `deploy/attacker/attack.sh reactor --plant` run **from ATK** can't connect
to either PLC. `show access-lists PT-ATTACKER-IN` shows the hits on line 50,
and the HMI and the 3D view keep agreeing because nothing has happened.

**2. Run the same attack from PC4.** It still works. PC4 legitimately needs
TCP 5021 to the SIS for HMI polling, the FILL valve and the reset pushbutton. A router ACL
filters on addresses and ports (L3/L4), so it can't tell a Modbus read of
`SIS_PV_HMI` from a write to the `SIS_OVERRIDE` bypass coil. Stopping that
needs a filter that understands Modbus function codes, which is beyond this
switch. That's deeper zone/conduit work, listed under Phase 3 in
[`ROADMAP.md`](ROADMAP.md).

**To restore the demo profile:**

```
configure terminal
ip access-list extended PT-ATTACKER-IN
 10 permit tcp host 10.0.5.15 host 10.0.2.12 eq 5020
 20 permit tcp host 10.0.5.15 host 10.0.3.13 eq 5021
end
```

## 10. Save, undo, factory reset

- **Save:** `copy running-config startup-config` (or `write memory`).
- **Drop the ACLs but keep routing:** on each SVI run
  `no ip access-group <NAME> in`, then `copy running-config startup-config`.
- **Factory reset** (this **erases everything** on the switch, so check
  `show running-config` first if it's shared kit):
  ```
  write erase
  delete flash:vlan.dat
  reload                     ! answer "no" if asked to save
  ```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `VlanNN` is `down/down` | No port in that VLAN is up. The PC is unplugged or powered off, or its cable is in the wrong port (`show interfaces status`, `show mac address-table interface Gi1/0/N`). |
| A PC can't ping its own gateway | The PC has the wrong IP or prefix, its profile isn't active (`nmcli con show --active`), or it's plugged into the wrong port or VLAN. |
| The gateway answers but other subnets don't | `ip route get <IP>` on the PC doesn't show `via 10.0.N.1`, `ip routing` is off (`show ip route connected` should list five `C` routes), or the far PC's firewall is blocking. |
| Worked before the ACLs, fails after | Look for a deny counter that's going up in `show access-lists`. Common causes: the PC's IP isn't the one in the ACL, or a port was changed through `PETROTWIN_*_PORT`. To isolate it, temporarily `no ip access-group` on the suspect SVI. |
| PC1 logs `LIVE unavailable` and falls back to fake PLCs | PC1 can't reach 5020/5021 **or** 8081/8082. `load_plant` needs the web UI ports as well as Modbus. |
| HMI reset does nothing | PC4 can't reach 8081/8082 (the reset restarts the runtimes through the web UI). |
| PC clocks won't sync to the switch | `show ntp status` should say `Clock is synchronized, stratum 3`. If the clients still refuse it, use the "set them together" fallback in [`DEPLOY-OFFLINE.md`](DEPLOY-OFFLINE.md) §A5. |

## Limits of these ACLs

- These are **stateless L3/L4 router ACLs**. `established` only checks TCP
  flags, so this isn't a stateful firewall.
- They limit **who** can reach each PLC port. They can't limit **what** gets
  sent over a permitted connection (Modbus reads vs. writes), as §9 shows.
- Per-layer VLANs plus these ACLs are the "basic segmentation" in Phase 2.5.
  IEC 62443 zones/conduits and pcap are Phase 3.
