# Arch workstation boot

Run from a freshly booted official Arch ISO: UEFI, x86_64, root, internet access.

```sh
curl -fsSLo /run/boot https://ixmorog.github.io/boot/run && bash /run/boot
```

On the first run, open `github.com/login/device` on your phone or another computer, enter the code and authorize GitHub CLI. Run the same command again to retry with the latest installer; your login is reused for the current live session.

- This repository contains only the launcher. Workstation settings and the installer stay in the private `ixmorog/homelab` repository.
- GitHub CLI uses a pinned version and SHA256. The installer is downloaded at an exact commit from `main`.
- Local login credentials stay in root-only `/run/workstation-boot/auth` on tmpfs until reboot. They are not copied to the installed system. To clear them earlier, run `bash /run/boot --logout`; this does not revoke the app's OAuth access on GitHub.
- Source files remain in `/run/homelab.*` until reboot, allowing individual stages to be retried.
- The installer formats a disk only after you select it and confirm.

Network or download failures preserve your login. If GitHub reports invalid credentials, use `--logout`, then run the launcher again to sign in.

Published with GitHub Pages from the root of `main`. No build tools or extra dependencies.

Tests: `python -m unittest discover -s tests` (isolated fixtures, no network or installation).
