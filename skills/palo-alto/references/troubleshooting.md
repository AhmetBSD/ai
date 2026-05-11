# Troubleshooting

## `ERROR: panos_provider undefined`

`~/.secrets/panos.yml` yok veya format hatalı. Kontrol:
```bash
cat ~/.secrets/panos.yml
# panos_provider:
#   ip_address: "..."
#   username: "..."
#   password: "{{ vault_panos_password }}"
```

## `MODULE FAILURE — ImportError: No module named pan.xapi`

venv'da `pan-os-python` eksik:
```bash
source ~/.palo-alto/venv/bin/activate
pip install pan-os-python xmltodict
```

## `SSL: CERTIFICATE_VERIFY_FAILED`

⚠️ **`validate_certs: false` YASAK (§1).** Doğru çözüm:

1. Internal CA cert'i sisteme yükle:
   ```bash
   sudo cp internal-ca.crt /usr/local/share/ca-certificates/
   sudo update-ca-certificates
   ```
2. Veya PAN-OS'a internal CA'dan imzalı cert yükle.
3. macOS keychain: `security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain internal-ca.crt`

## `panos_security_rule: rule order wrong`

`location` parametresi default `bottom`. İstediğin yere koymak için:
```yaml
location: before
existing_rule: "deny-all"
```

## `commit hangs / timeout`

PAN-OS'ta commit job'ı arka planda devam ediyor. Sync yerine async commit:
```yaml
- name: Commit (fire and forget)
  paloaltonetworks.panos.panos_commit_firewall:
    provider: "{{ panos_provider }}"
    sync: false
  register: commit_result

- name: Poll job until FIN
  paloaltonetworks.panos.panos_op:
    provider: "{{ panos_provider }}"
    cmd: "<show><jobs><id>{{ commit_result.jobid }}</id></jobs></show>"
  register: job_status
  until: "'FIN' in job_status.stdout"
  retries: 30
  delay: 10
```

## Panorama vs Firewall — modül seçimi

| Hedef | `device_group` param | Modül |
|-------|----------------------|-------|
| Firewall direkt | yok | `panos_security_rule`, vs. |
| Panorama → Device Group | `device_group: "DG1"` | aynı modüller + `device_group` |
| Panorama → Template | `template: "T1"` | network/interface modülleri |

## Commit-all (Panorama)

```yaml
- paloaltonetworks.panos.panos_commit_panorama:
    provider: "{{ panos_provider }}"
    description: "push DG1"
    style: "device group"
    name: "DG1"
    include_template: true
    devices: []   # boş = tüm DG
```

## API key vs password — hangisi?

- **Password:** Provider'da kullanırken her çağrıda authenticate olur.
- **API key:** Bir kere üret, sonra kullan. Password değişse de geçerli. Hızlı.
- Production'da **API key** öner; rotation kolay.

## DEBUG seviye log

```bash
ANSIBLE_LOG_PATH=/tmp/ansible.log \
ANSIBLE_DEBUG=true \
  ./scripts/run-playbook.sh playbooks/foo.yml
```
