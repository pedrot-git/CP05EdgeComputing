# CP5 Edge Dashboard

Dashboard web para leituras do ESP32 salvas no FIWARE STH-Comet.

## Rodar

```bash
npm run start
```

Acesse:

```text
http://localhost:3000
```

## Configuracao

Os valores padrao seguem o notebook original:

```bash
STH_URL=http://34.95.131.219:8666
ORION_URL=http://34.95.131.219:1026
FIWARE_SERVICE=smart
FIWARE_SERVICE_PATH=/
ENTITY_TYPE=WineCellar
ENTITY_ID=urn:ngsi-ld:WineCellar:001
```

Para trocar algum valor:

```bash
STH_URL=http://seu-servidor:8666 npm run start
```

## Recursos

- Grafico unico para luminosidade, temperatura e umidade.
- Cartoes com as ultimas leituras.
- Alertas calculados pelos limites da vinheria.
- Atualizacao automatica com intervalo configuravel.
- Proxy local para evitar bloqueio de CORS no navegador.
- Comandos remotos `alert_on`, `alert_off` e `silence_buzzer`.
