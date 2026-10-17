# Arch workstation boot

Run from a freshly booted official Arch ISO: UEFI, x86_64, root, internet access.

```sh
curl -fsSLo /run/boot https://ixmorog.github.io/boot/run && bash /run/boot
```

Open `github.com/login/device` on your phone or another computer, enter the code and authorize GitHub CLI. The installer wizard will then start.

- This repository contains only the launcher. Workstation settings and the installer stay in the private `ixmorog/homelab` repository.
- GitHub CLI uses a pinned version and SHA256. The installer is downloaded at an exact commit from `main`.
- Local login credentials stay in RAM and are removed before the wizard starts. This does not revoke the app's OAuth access on GitHub.
- Source files remain in `/run/homelab.*` until reboot, allowing individual stages to be retried.
- The installer formats a disk only after you select it and confirm.

Published with GitHub Pages from the root of `main`. No build tools or extra dependencies.
