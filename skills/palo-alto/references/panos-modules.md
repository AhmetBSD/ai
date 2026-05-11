# paloaltonetworks.panos — Ansible Modül Listesi

Sık kullanılan modüller. Tam liste için:
```bash
ansible-doc -l paloaltonetworks.panos
```

## Object / Address
| Modül | Amaç |
|-------|------|
| `panos_address_object` | Address object CRUD |
| `panos_address_group` | Address group (static / dynamic) |
| `panos_service_object` | Service (tcp/udp/sctp + port) |
| `panos_service_group` | Service group |
| `panos_tag_object` | Tag (color + comment) |
| `panos_application_object` | Custom application |

## Policy
| Modül | Amaç |
|-------|------|
| `panos_security_rule` | Security policy rule |
| `panos_nat_rule` | NAT rule (source/destination) |
| `panos_pbf_rule` | Policy-based forwarding |
| `panos_decryption_rule` | SSL decryption policy |
| `panos_authentication_rule` | Authentication policy |

## Network
| Modül | Amaç |
|-------|------|
| `panos_interface` | Ethernet L3/L2 interface |
| `panos_zone` | Security zone |
| `panos_virtual_router` | Virtual router |
| `panos_static_route` | Static route |
| `panos_bgp` | BGP config |
| `panos_bgp_peer` | BGP peer |
| `panos_ike_gateway` | IPsec IKE gateway |
| `panos_ipsec_tunnel` | IPsec tunnel |

## Device / Operations
| Modül | Amaç |
|-------|------|
| `panos_commit_firewall` | Commit candidate → running |
| `panos_commit_panorama` | Panorama commit + push |
| `panos_op` | Operational command (XML) |
| `panos_export` | Config / report export |
| `panos_import` | Config / cert import |
| `panos_api_key` | API key generation |
| `panos_software` | PAN-OS upgrade |
| `panos_check` | Connectivity check |

## Profil (Security Profiles)
| Modül | Amaç |
|-------|------|
| `panos_url_filtering` | URL filtering profile |
| `panos_anti_spyware` | Anti-spyware profile |
| `panos_file_blocking` | File blocking profile |
| `panos_security_profile_group` | Profile group |

## Örnek — API key üretimi (bir kere)

```yaml
- name: Generate API key
  paloaltonetworks.panos.panos_api_key:
    provider:
      ip_address: "fw01.example.com"
      username: "admin"
      password: "{{ vault_panos_password }}"
  register: api_key_result

- name: Save key
  ansible.builtin.copy:
    content: "{{ api_key_result.api_key }}"
    dest: "~/.secrets/panos.apikey"
    mode: '0600'
```

Sonrasında provider:
```yaml
panos_provider:
  ip_address: "fw01.example.com"
  api_key: "{{ lookup('file', '~/.secrets/panos.apikey') }}"
```
