# Changelog

## 1.0.1-mode-latency-fix

- Instrumentado o caminho AUTO/MANUAL com `time.perf_counter()` na GUI, servidor, IA e TP nativo.
- Eliminado o processo Python e a conexão WebSocket efêmeros criados para cada comando da GUI.
- Adicionado sender WebSocket persistente com fila e reconexão local.
- Adicionado indicador visual `MODO: AUTO/MANUAL` e destaque do botão ativo.
- Preservados PLAY/PAUSE, posição, roteiro, Whisper e motor de scroll durante as transições.

## 1.0.0-portfolio-stable

- Criada uma distribuição separada a partir das versões experimentais do Teleprompter IA.
- Preservado o reconhecimento com Faster Whisper e o alinhamento local fala/roteiro.
- Incorporado o motor de scroll nativo da variante Broadcast Fix, baseado em tempo, velocidade, aceleração e amortecimento.
- Removida toda dependência direta de iNews, FTP, FTPS, rxnet, polling de fila e parsing NSML.
- Corrigida a reinicialização completa do estado de progresso da IA.
- Centralizado o fluxo lógico de reload do roteiro.
- Separados os estados AUTO/MANUAL e PLAY/PAUSE.
- Adicionado estado explícito de final de roteiro.
- Alterado o bind padrão de HTTP e WebSocket para `127.0.0.1`.
- Adicionada exposição opcional à LAN por configuração explícita.
- Removidos caminhos pessoais e instalação automática de dependências.
- Adicionados roteiro neutro de demonstração, documentação e arquivos de empacotamento.
