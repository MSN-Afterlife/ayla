# OVH host-key pinning

Before enabling either deployment workflow, obtain the OVH SSH host public key or fingerprint through a previously verified out-of-band channel. Review it, then add the resulting OpenSSH known_hosts entry as `.github/known_hosts/ovh`.

The workflows intentionally fail if that file is absent. They use `StrictHostKeyChecking` defaults and never run `ssh-keyscan` during deployment.
