# Teleprompter AI — Speech-Synchronized Broadcast Teleprompter

Teleprompter inteligente desenvolvido em Python que acompanha automaticamente a leitura do apresentador por reconhecimento de fala e alinhamento com o roteiro.

![Teleprompter nativo em operação](docs/images/teleprompter.png)

## Visão geral

O Teleprompter AI combina reconhecimento de fala, alinhamento textual e um motor de rolagem suave para acompanhar o apresentador sem exigir avanço manual constante. O operador pode alternar entre automação e controle manual durante uma operação de broadcast.

## Demonstração

### Interface de operação

![Interface de operação do Teleprompter AI](docs/images/interface.png)

### Teleprompter nativo

![Teleprompter nativo com HUD operacional](docs/images/teleprompter.png)

### Controle remoto

![Controle remoto para celular](docs/images/remote.png)

## Como funciona

```text
Microfone
    ↓
Whisper
    ↓
Normalização da fala
    ↓
Alinhamento com roteiro
    ↓
word_index
    ↓
Mapa visual / interpolação sub-linha
    ↓
target_y
    ↓
Motor de rolagem
    ↓
Teleprompter
```

## Principais recursos

- Reconhecimento de fala
- Alinhamento da fala ao roteiro
- Modos AUTO e MANUAL
- PLAY/PAUSE e RESET
- Avançar/Voltar para operação manual
- Teleprompter nativo
- Controle remoto
- Espelhamento para vidro de teleprompter
- Comunicação WebSocket e servidor HTTP
- Biblioteca de roteiros
- HUD AUTO/MANUAL
- Percentual baseado na posição visual
- Interpolação sub-linha a partir de `word_index`
- Proteção de sessão e reset por `session_id`

## Arquitetura

- `TP_Control_GUI.py`: interface Tkinter, gerenciamento dos processos, biblioteca de roteiros, controle operacional e renderização do TP nativo.
- `main_align_ws.py`: captura de áudio, transcrição com Whisper, normalização, alinhamento do texto falado e envio de progresso/`word_index`.
- `scroll_server.py`: servidor WebSocket que distribui comandos e atualizações entre IA, teleprompter e controles.
- `remote_by.html`: controle remoto gerado pela aplicação com PIN operacional temporário.

O projeto é atualmente organizado nesses componentes principais, sem apresentar como concluída uma modularização que ainda faz parte do roadmap. Veja [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) para o fluxo técnico.

## Desafios técnicos resolvidos

1. **Latência dos comandos** — uma conexão WebSocket persistente evita o custo de reconectar a cada comando.
2. **Continuidade do scroll** — o destino solicitado (`target_y`) é separado da posição física atual, permitindo que o motor continue perseguindo o alvo entre mensagens.
3. **Palavra e linha visual** — o `word_index` do alinhador é convertido para o mapa das linhas efetivamente renderizadas.
4. **RESET e pacotes antigos** — o `session_id` impede que mensagens de uma sessão anterior contaminem a sessão corrente.
5. **Rolagem suave** — velocidade, aceleração, damping e intervalo de tempo (`dt`) compõem a dinâmica do movimento.
6. **AUTO/MANUAL** — ambos usam a mesma física; muda apenas a origem do `target_y`.
7. **Resposta inicial** — a posição da palavra dentro da linha é interpolada, permitindo reação visual antes da mudança para a linha seguinte.

## Tecnologias

- Python 3.11
- Tkinter
- faster-whisper
- NumPy
- sounddevice
- RapidFuzz
- websockets
- psutil
- Pillow
- qrcode
- tkinterweb

## Testes

**62 testes automatizados aprovados** na versão atualmente validada.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Não foi medida cobertura percentual.

## Instalação

Ambiente validado: Windows com Python 3.11 e microfone configurado.

```powershell
cd C:\caminho\para\TP_AI
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python TP_Control_GUI.py
```

O primeiro carregamento do modelo Whisper pode precisar baixar seus arquivos. O `requirements.txt` contém as dependências externas importadas pelo projeto; Tkinter faz parte da instalação padrão do Python para Windows.

## Uso

1. Selecione ou confirme o interpretador Python na interface.
2. Carregue um roteiro da biblioteca ou escolha um arquivo compatível.
3. Inicie os serviços HTTP e WebSocket.
4. Abra o TP nativo.
5. Inicie IA/Voz.
6. Faça a leitura em AUTO para acompanhamento por fala.
7. Use MANUAL, Avançar/Voltar e PLAY/PAUSE como controles operacionais.

## Segurança

Esta versão é destinada a demonstração e ambientes controlados. HTTP e WebSocket usam `127.0.0.1` por padrão. A exposição na rede local por `TELEPROMPTER_ALLOW_LAN=1` deve ser feita apenas em uma rede confiável e com controles externos adequados, pois o protocolo atual não oferece autenticação de rede forte nem transporte TLS.

Arquivos locais, estado de execução, ambientes virtuais e o controle remoto gerado com PIN temporário são excluídos pelo `.gitignore`.

## Roadmap

- Integração com iNews/Autoscript
- Modularização da arquitetura
- Empacotamento da aplicação
- Autenticação de rede
- Telemetria operacional
- Testes com diferentes locutores e ambientes acústicos

## Autor

Paulo Abel Pereira Alves

Engenharia de Telecomunicações | Broadcast | Python | Inteligência Artificial
