# Plano de testes locais

## Testes sem hardware

- Validar a sintaxe de todos os arquivos Python.
- Iniciar o servidor WebSocket apenas em `127.0.0.1`.
- Iniciar o servidor HTTP apenas em `127.0.0.1`.
- Verificar o carregamento do roteiro de demonstração.
- Validar mensagens de AUTO, MANUAL, PLAY, PAUSE, RESET e RELOAD.
- Confirmar que o progresso é limitado ao intervalo de 0% a 100%.

## Testes manuais com hardware

- Selecionar o dispositivo de áudio correto.
- Ler o roteiro completo em português e observar o alinhamento.
- Alternar AUTO/MANUAL durante a leitura.
- Confirmar que PAUSE não bloqueia passos manuais.
- Testar TP nativo em um segundo monitor e modo espelhado.
- Testar o controle remoto na LAN somente após habilitar explicitamente esse modo.
- Executar uma sessão contínua de pelo menos 60 minutos.

