# PAN-OS XML API Cheatsheet

`scripts/xml-api.sh` ile kullanım. Salt-okunur işlemler için ideal.

## Auth — API Key al

```bash
curl --cacert "$PANOS_CA_BUNDLE" \
  "https://$PANOS_HOST/api/?type=keygen&user=admin&password=YOUR_PW"
# Response: <response status="success"><result><key>LUFRPT...</key></result></response>
```

API key'i `~/.secrets/panos.apikey` (chmod 600) içine yaz.

## Operasyonel Sorgular (type=op)

| Komut | XML |
|-------|-----|
| Sistem bilgisi | `<show><system><info/></system></show>` |
| Çalışan job'lar | `<show><jobs><all/></jobs></show>` |
| Interface durumu | `<show><interface>all</interface></show>` |
| Routing table | `<show><routing><route/></routing></show>` |
| BGP peers | `<show><routing><protocol><bgp><peer/></bgp></protocol></routing></show>` |
| Session count | `<show><session><info/></session></info></show>` |
| HA durumu | `<show><high-availability><state/></high-availability></show>` |
| Commit job sonucu | `<show><jobs><id>JOB_ID</id></jobs></show>` |

Örnek:
```bash
PANOS_HOST=fw01.example.com \
PANOS_API_KEY=$(cat ~/.secrets/panos.apikey) \
PANOS_CA_BUNDLE=/etc/ssl/internal-ca.pem \
  ./scripts/xml-api.sh op '<show><system><info/></system></show>'
```

## Config Sorguları (type=config, action=get)

| xpath | İçerik |
|-------|--------|
| `/config/devices/entry/vsys/entry[@name='vsys1']/address` | Tüm address object |
| `/config/devices/entry/vsys/entry[@name='vsys1']/rulebase/security` | Security rules |
| `/config/devices/entry/network/interface/ethernet` | Ethernet interface'leri |
| `/config/devices/entry/vsys/entry[@name='vsys1']/zone` | Zone'lar |

## Commit (type=commit)

```bash
./scripts/xml-api.sh commit "deploy from script"
# Returns job ID — poll with: op '<show><jobs><id>JOBID</id></jobs></show>'
```

## Rapor / Log Sorgu

URL pattern: `/api/?type=log&log-type=traffic&query=...&nlogs=20`

| log-type | Açıklama |
|----------|----------|
| `traffic` | Traffic log |
| `threat`  | Threat log |
| `url`     | URL filtering log |
| `data`    | Data filtering log |
| `system`  | System log |
| `config`  | Config change audit |

Query örnekleri:
- `(addr.src in 10.0.0.0/24)` — source IP
- `(app eq web-browsing)` — application
- `(action eq deny)` — deny edilenler

## Debug

```bash
# Tüm response'u görmek için:
./scripts/xml-api.sh op '<show><system><info/></system></show>' | xmllint --format -

# Job tamamlanma bekleme:
JOBID=12345
while true; do
  STATUS=$(./scripts/xml-api.sh op "<show><jobs><id>$JOBID</id></jobs></show>" \
    | xmllint --xpath 'string(//job/status)' -)
  echo "$STATUS"
  [[ "$STATUS" == "FIN" ]] && break
  sleep 2
done
```
