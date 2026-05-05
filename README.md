# CP05 Edge Computing - Vinheria Inteligente

Projeto de Edge Computing e IoT para monitoramento ambiental de uma vinheria, usando ESP32 no Wokwi, sensores de luminosidade, temperatura e umidade, FIWARE para contexto/historico e um dashboard Flask para visualizacao e controle remoto.

> Simulacao Wokwi: [https://wokwi.com/projects/463141675910701057](https://wokwi.com/projects/463141675910701057)

## Integrantes

| RM | Nome |
| --- | --- |
| 567680 | Pedro Henrique Tavares Viana |
| 567855 | David Ernesto Mogollon Gama |
| 566949 | Roger De Carvalho Paiva |

## Visao Geral

O sistema acompanha as condicoes ambientais de uma vinheria e identifica situacoes fora dos limites ideais de conservacao. O ESP32 publica as leituras via MQTT, o FIWARE armazena o contexto e o historico, e o dashboard calcula os triggers com base nos dados historicos do STH-Comet.

Quando uma variavel sai dos parametros, o dashboard envia um comando remoto para o ESP32 pelo Orion/IoT Agent. O dispositivo entao aciona o LED azul e o buzzer com padroes diferentes para temperatura, umidade ou luminosidade.

## Arquitetura



Esta arquitetura foi adaptada para o projeto a partir do modelo de referencia FIWARE apresentado no repositorio do professor Fabio Cabrini: [FIWARE Descomplicado](https://github.com/fabiocabrini/fiware).

## Componentes

| Camada | Tecnologia | Funcao |
| --- | --- | --- |
| IoT | ESP32 no Wokwi | Leitura dos sensores e acionamento dos atuadores |
| Sensores | LDR e DHT22 | Luminosidade, temperatura e umidade |
| Atuadores | LED azul e buzzer | Indicacao local de alerta |
| Mensageria | MQTT / Mosquitto | Transporte entre ESP32 e FIWARE |
| FIWARE | IoT Agent MQTT | Conversao MQTT para NGSIv2 |
| FIWARE | Orion Context Broker | Entidade atual e comandos remotos |
| FIWARE | STH-Comet | Historico temporal dos atributos |
| Aplicacao | Python Flask | Dashboard, graficos, alertas e controle |

## Limites Monitorados

| Variavel | Condicao ideal | Alerta |
| --- | --- | --- |
| Temperatura | 12 C a 18 C | Fora da faixa |
| Umidade | 60% a 80% | Fora da faixa |
| Luminosidade | Ate 30% | Acima do limite |

## Comportamento dos Alertas

| Trigger | Comando enviado | Padrao do buzzer |
| --- | --- | --- |
| Temperatura fora do limite | `wine001@buzzer|temperature` | Bip longo |
| Umidade fora do limite | `wine001@buzzer|humidity` | Dois bips |
| Luminosidade alta | `wine001@buzzer|luminosity` | Tres bips |
| Ambiente normal | `wine001@buzzer|none` | Desligado |

## Estrutura do Projeto

```text
.
├── app.py
├── requirements.txt
├── public/
│   ├── index.html
│   ├── app.js
│   └── styles.css
├── firmware/
│   └── wine001/
│       └── wine001.ino
```

## Como Executar o Dashboard

Instale a dependencia Python:

```bash
python -m pip install -r requirements.txt
```

Inicie o Flask:

```bash
python app.py
```

Acesse no navegador:

```text
http://localhost:5000
```

Se estiver rodando dentro da VM:

```text
http://34.39.150.225:5000
```

## Configuracao

Os valores padrao apontam para a VM utilizada no projeto:

```bash
VM_IP=34.39.150.225
STH_URL=http://34.39.150.225:8666
ORION_URL=http://34.39.150.225:1026
FIWARE_SERVICE=smart
FIWARE_SERVICE_PATH=/
ENTITY_TYPE=WineCellar
ENTITY_ID=urn:ngsi-ld:WineCellar:001
```

Para trocar a VM:

```bash
VM_IP=seu-ip python app.py
```

Para trocar a porta local:

```bash
PORT=8000 python app.py
```

## Firmware ESP32

O sketch principal esta em:

```text
firmware/wine001/wine001.ino
```

No Wokwi, a rede deve permanecer:

```cpp
const char* WIFI_SSID = "Wokwi-GUEST";
const char* WIFI_PASSWORD = "";
```

Topicos usados pelo dispositivo:

| Finalidade | Topico |
| --- | --- |
| Publicacao de atributos | `/TEF/wine001/attrs` |
| Comandos remotos | `/TEF/wine001/cmd` |
| Luminosidade | `/TEF/wine001/attrs/l` |
| Temperatura | `/TEF/wine001/attrs/t` |
| Umidade | `/TEF/wine001/attrs/h` |

## Dashboard

O dashboard oferece:

- Cards com as ultimas leituras de luminosidade, temperatura e umidade.
- Grafico historico consultando o STH-Comet.
- Linhas de limite para cada variavel.
- Lista de alertas historicos.
- Atualizacao automatica.
- Botoes para testar os comandos remotos do buzzer.
- Sincronizacao automatica do alerta atual com o ESP32.

## Validacao

Durante os testes, o endpoint de comando do dashboard retornou sucesso para o envio:

```text
POST /api/command
{"command": "buzzer", "value": "temperature"}

HTTP 200
{"ok": true, "status": 204}
```

No terminal serial do Wokwi, o ESP32 deve exibir:

```text
Mensagem recebida no topico /TEF/wine001/cmd: wine001@buzzer|temperature
Comando remoto BUZZER executado: temperature
```

## Referencias

- [FIWARE Descomplicado - Fabio Cabrini](https://github.com/fabiocabrini/fiware)
- [FIWARE Foundation](https://www.fiware.org/)
- [Wokwi ESP32 Simulator](https://wokwi.com/)
