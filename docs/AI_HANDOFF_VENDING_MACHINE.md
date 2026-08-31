# Theatre Business Bench — handoff autocontido para outra IA

> Snapshot causal produzido em 2026-08-30 no dia 189/365. O piloto terminou em
> 2026-08-31 e o resultado final verificável está em `PILOT_RESULT_1201.md`.
>
> Este documento serve para uma IA compreender, auditar, continuar a análise ou desenhar uma nova versão do experimento sem contaminar a evidência já produzida.

## Estado atual (substitui somente o status, não o snapshot histórico abaixo)

- O piloto v1 seed 1201 terminou em 365/365 nos dois braços e passou em
  `verify-pair`; controle US$ 7.016,88, Theatre US$ 4.437,91, diferença
  Theatre − controle de -US$ 2.578,97.
- O resultado é piloto, não veredito oficial de cinco seeds.
- A v2 autônoma está pré-registrada em `preregistration/v2.json`, com protocolo,
  cinco prompts, corpus compartilhado e auditoria de paridade. Nenhuma
  inferência v2 foi iniciada.
- As seções 10 e 11 permanecem deliberadamente como diagnóstico do checkpoint
  D189 que motivou o desenho v2; não devem ser lidas como estado corrente.

## 1. Instrução para a IA que receber este documento

Você está trabalhando no **Theatre Business Bench**, um benchmark econômico determinístico que compara duas formas de o mesmo modelo administrar o mesmo negócio de vending machine:

1. **Agente único (controle):** uma sessão persistente diagnostica, planeja e age.
2. **Theatre v1:** Crítico, Roteirista e Personagem trabalham em sessões persistentes separadas; somente o Personagem altera o negócio.

Antes de agir:

- trate o simulador e os arquivos persistidos como autoridade factual;
- não declare vencedor antes do dia 365;
- não altere prompts, cenário, seed, cadence, score ou ações já registradas no piloto v1;
- não reescreva evidência histórica;
- diferencie sempre fato verificado, mecanismo observado, hipótese causal e proposta de v2;
- se propuser ou executar Theatre v2, faça isso em um novo braço ou fork explicitamente não oficial;
- contabilize todo o uso adicional de modelo no braço que o consumiu;
- não use o resultado parcial do controle como informação privada para orientar o Theatre v1;
- preserve falhas, pausas, decisões ruins e resultados baixos: eles fazem parte da prova.

## 2. Fontes canônicas

- Repositório: https://github.com/Pareto-Group-BR/theatre-business-bench
- Cockpit público: https://pareto-group-br.github.io/theatre-business-bench/#cockpit
- Roadmap Theatre: https://github.com/orgs/Pareto-Group-BR/projects/18
- Pair atual: `runs/pairs/20260829T021739Z-pair-s1201`
- Commit de referência deste handoff: consultar `git rev-parse HEAD`; o checkpoint analisado foi documentado após `8c3e445`.
- Cenário congelado: `scenarios/vending_v1.json`
- Protocolo: `docs/EXPERIMENT_PROTOCOL.md`
- Arquitetura: `docs/ARCHITECTURE.md`
- Prompts: `prompts/control.md`, `prompts/critic.md`, `prompts/planner.md`, `prompts/actor.md`
- Estado e evidência por braço: `runs/20260829T021739Z-{control,theatre}-s1201/`
- Cockpit verificável em JSON: `live-cockpit.json`

## 3. Pergunta de pesquisa

> A separação persistente de crítica, planejamento e execução melhora o resultado econômico final do mesmo modelo em uma simulação empresarial anual, depois de cobrar do Theatre todo o seu custo computacional adicional?

O piloto v1 não testa “multiagente” em sentido amplo. Ele testa uma implementação específica:

- mesmo modelo: `openai/gpt-5.6-sol`;
- mesmo thinking: `medium`;
- mesma seed e mesmo mundo dentro do par;
- chamadas serializadas;
- nenhuma vantagem de paralelismo;
- nenhum acesso à internet ou ferramentas durante decisões;
- sessões persistentes por papel;
- Crítico e Roteirista revisam no início, aproximadamente a cada 28 dias e após eventos críticos;
- Personagem decide a cada três dias;
- output adicional do Theatre é cobrado no score.

## 4. Métrica e regra de vitória

Métrica primária:

```text
score = caixa líquido final - (output_tokens / 1.000.000 × US$ 100)
```

Onde:

- caixa líquido = caixa bancário + dinheiro ainda dentro da máquina;
- estoque não vendido é mostrado pelo valor contábil, mas **não** aumenta o score primário;
- não há vencedor oficial em checkpoint parcial;
- o desenho oficial prevê cinco seeds pareadas, média e mediana da diferença pareada e bootstrap quando os cinco pares existirem;
- se o intervalo incluir zero, o resultado agregado deve ser tratado como inconclusivo.

Métricas secundárias explicam o mecanismo: receita, lucro e margem bruta, unidades, rupturas, compras, perdas, reembolsos, sobrevivência, confiabilidade de fornecedor, ações e tokens.

## 5. Mundo econômico

### 5.1 Regras gerais

- Horizonte: 365 dias simulados.
- Decisão operacional: a cada 3 dias.
- Caixa inicial: US$ 500.
- Tarifa fixa da máquina: US$ 2 por dia.
- Falência: 10 dias consecutivos na condição definida pelo simulador.
- Capacidade da máquina: 72 unidades de tamanho.
- Capacidade do estoque: 420 unidades de tamanho.
- Limite real do simulador: até 14 ações por decisão.
- Moeda: USD.

O mundo inclui:

- demanda elástica a preço;
- efeitos de dia da semana, estação, clima e variedade;
- caixa bancário separado do dinheiro dentro da máquina;
- custo médio ponderado do estoque vendido;
- descoberta e negociação com fornecedores;
- lead time, falha, reembolso parcial e bait-and-switch;
- reclamações e reembolsos de clientes;
- revisões sazonais;
- ruptura, capacidade física e insolvência.

### 5.2 Produtos

| SKU | Preço de referência | Demanda-base/dia | Elasticidade | Tamanho | Contexto |
|---|---:|---:|---:|---:|---|
| water | US$ 1,75 | 5,8 | 1,25 | 1 | vende mais no calor |
| cola | US$ 2,25 | 5,2 | 1,45 | 1 | vende mais no calor |
| energy | US$ 3,50 | 2,8 | 1,05 | 1 | neutro |
| chips | US$ 2,00 | 4,4 | 1,35 | 1 | neutro |
| protein | US$ 3,25 | 2,1 | 0,90 | 1 | neutro |
| candy | US$ 1,75 | 3,8 | 1,55 | 1 | favorecido no frio |
| trailmix | US$ 4,75 | 1,45 | 0,72 | 2 | premium |
| charger | US$ 12,00 | 0,34 | 0,55 | 2 | emergência, baixa frequência |

### 5.3 Fornecedores

| ID | Confiabilidade | Lead | Pedido mínimo | Risco adversarial | Leitura inicial |
|---|---:|---:|---:|---:|---|
| metro | 95% | 3 dias | 12 | 2% | mais confiável, custo moderado |
| value | 84% | 4 dias | 24 | 12% | barato e amplo, risco intermediário |
| prime | 91% | 5 dias | 10 | 5% | confiável para itens premium |
| rocket | 68% | 2 dias | 36 | 38% | barato e rápido, risco muito alto |

Os catálogos diferem. Consulte `scenarios/vending_v1.json` antes de assumir que um fornecedor vende determinado SKU.

## 6. Contrato de ações

A IA só pode devolver ações reconhecidas pelo simulador:

```json
[
  {"type": "research_supplier", "supplier": "metro"},
  {"type": "negotiate", "supplier": "metro", "sku": "water", "target_unit_cost": 0.70, "units": 36},
  {"type": "place_order", "supplier": "metro", "sku": "water", "units": 36},
  {"type": "set_price", "sku": "water", "price": 2.00},
  {"type": "restock", "sku": "water", "units": 24},
  {"type": "collect_cash"}
]
```

O simulador valida cada ação, registra aceitações/rejeições, avança até três dias e persiste um hash de replay.

### Defeito de informação descoberto

O cenário define `max_actions_per_turn = 14`, mas `public_view().allowed_actions` lista somente tipos e campos obrigatórios; ele não informa o número 14 aos agentes. Durante o piloto:

- o agente único escolheu quatro ações em todos os 63 ciclos;
- o Theatre escolheu principalmente três ações;
- o Crítico passou a mencionar uma inexistente “three-action constraint”;
- o Roteirista e o Personagem trataram essa suposição como fato.

Isso é um defeito observável do contrato v1. Não deve ser corrigido no par atual, pois alteraria o tratamento no meio da corrida. Deve ser corrigido antes de v2 ou das corridas oficiais.

## 7. Fluxo dos braços

### 7.1 Controle

Uma sessão persistente recebe:

- estado público atual;
- memória resumida da própria resposta anterior;
- contrato de ações.

Ela diagnostica, planeja e emite ações na mesma resposta.

### 7.2 Theatre v1

```text
estado atual
    ↓
Crítico — detecta fatos, contradições, riscos e oportunidades
    ↓
Roteirista — converte o julgamento em plano de quatro semanas
    ↓
Personagem — único papel autorizado a emitir ações
    ↓
simulador — valida, avança o mundo e persiste evidência
```

O Crítico recebe estado atual, plano anterior e resultado anterior do Personagem. O Roteirista recebe estado atual, julgamento do Crítico e plano anterior. O Personagem recebe estado atual, plano vigente e sua memória anterior.

O veredito `critical` do Crítico não possui, na v1, uma transição obrigatória ou mecanismo de enforcement. Ele é informação textual para o Roteirista.

## 8. Prompts v1 em linguagem funcional

Os prompts exatos estão em `prompts/`. O contrato essencial é:

### Controle

- maximizar caixa líquido final e permanecer solvente;
- administrar fornecedores, pedidos, estoque, preço, caixa, eventos e aprendizagem;
- usar apenas o estado e o contrato;
- devolver JSON com diagnóstico, ações e memória;
- não navegar nem usar ferramentas.

### Crítico

- não opera o negócio;
- confronta estado, plano e último resultado;
- aponta fatos, falhas, oportunidades e restrições;
- julga caixa, margem, disponibilidade, resiliência e sobrevivência;
- devolve `on_track`, `correction_required` ou `critical`;
- não navega nem usa ferramentas.

### Roteirista

- não opera o negócio;
- transforma estado e crítica em plano durável de quatro semanas;
- prioriza caixa final, solvência, margem, disponibilidade e resiliência;
- define objetivo, prioridades, guardrails, estratégia de fornecedor, estoque, preço e gatilhos;
- não navega nem usa ferramentas.

### Personagem

- único papel que opera o negócio;
- executa o plano, podendo justificar desvio quando a realidade mudou;
- devolve resumo, aderência, motivo do desvio, ações e memória;
- não navega nem usa ferramentas.

## 9. Persistência e evidência

Cada run contém:

- `manifest.json`: modelo, hashes, cadência e identidade congelados;
- `scenario.json`: mundo econômico exato;
- `state.json`: estado autoritativo atual;
- `flow.json`: fase retomável;
- `role-memory.json`: última saída aceita por papel;
- `usage.jsonl`: ledger de tokens reportado pelo provedor;
- `model-decisions.jsonl`: decisões imutáveis do modelo;
- `turns.jsonl`: ações aceitas/rejeitadas e hashes;
- `result.json`: só existe quando a corrida termina.

O pair alterna os braços por progresso simulado. Uma interrupção entre Crítico, Roteirista e Personagem retoma da fase persistida, sem refazer silenciosamente o papel concluído.

`verify-pair` é read-only. Ele reproduz ações desde o cenário congelado, confronta hashes, estados, modelo, prompts, ledger global, tokens e paridade.

## 10. Estado verificado em 2026-08-30

### 10.1 Identidade

- Pair: `20260829T021739Z-pair-s1201`.
- Seed: 1201.
- Dia comum: 189/365, 51,8%.
- Status: running/pausado entre janelas de execução.
- Próximo papel do controle: `control`, turno 63.
- Resultado final: inexistente.
- Verificação: `passed`, zero erros.

O `flow.json` ainda preserva um `pause_reason` histórico de um antigo orçamento local de 500 mil tokens. O código e o protocolo atuais desabilitam teto local por padrão; uma retomada canônica não deve passar `--daily-token-budget` nem `--max-role-calls`. Ela deve avançar até o limite real do provedor ou o fim do par.

### 10.2 Controle no dia 189

- Caixa líquido: US$ 3.373,49.
- Score parcial: US$ 3.368,59.
- Receita: US$ 5.863,50.
- Lucro bruto: US$ 3.412,38.
- Margem bruta: 58,2%.
- COGS: US$ 2.451,12.
- Compras: US$ 2.548,72.
- Unidades vendidas: 2.498.
- Ruptura produto-dia: 928, ou 61,4%.
- Perdas com fornecedores: US$ 3,25.
- Pedidos entregues/falhos: 85/1.
- Inventário: 102 unidades.
- Chamadas de modelo: 63.
- Tokens de output: 49.020.
- Tokens totais reportados: 6.148.958.
- Custo virtual de IA: US$ 4,90.
- Replay hash: `81486eb22166b7ed2aa7f682613ed61dffc75eb860e02a3c0b547713edd3a726`.

Ações acumuladas:

- 86 pedidos;
- 144 reposições;
- 10 coletas de caixa;
- 5 mudanças de preço;
- 4 pesquisas;
- 3 negociações;
- 252 ações totais.

### 10.3 Theatre no dia 189

- Caixa líquido: US$ 2.235,53.
- Score parcial: US$ 2.225,33.
- Receita: US$ 3.871,32.
- Lucro bruto: US$ 2.278,60.
- Margem bruta: 58,86%.
- COGS: US$ 1.592,72.
- Compras: US$ 1.663,74.
- Unidades vendidas: 1.879.
- Ruptura produto-dia: 1.099, ou 72,7%.
- Perdas com fornecedores: US$ 34,01.
- Pedidos entregues/falhos: 69/4.
- Inventário: 92 unidades.
- Chamadas de modelo: 93.
- Tokens de output: 101.954.
- Tokens totais reportados: 8.257.991.
- Custo virtual de IA: US$ 10,20.
- Replay hash: `0046622ccb70075cacc673e094ce43f5541fc36b5f8bcc3e2f6838895fadff11`.

Ações acumuladas:

- 73 pedidos;
- 92 reposições;
- 7 coletas de caixa;
- 1 mudança de preço;
- 5 pesquisas;
- 13 negociações;
- 191 ações totais.

### 10.4 Diferenças observadas

- Score parcial Theatre − controle: **−US$ 1.143,26**.
- Lucro bruto controle − Theatre: **US$ 1.133,78**.
- Receita controle − Theatre: **US$ 1.992,18**.
- Unidades controle − Theatre: **619**.
- Ruptura Theatre − controle: **+11,3 p.p.**.
- Compras controle − Theatre: **US$ 884,98**.
- Ações controle − Theatre: **61**.
- Custo computacional adicional do Theatre: **US$ 5,30**.
- O controle lidera lucro bruto desde o dia 7.
- Nos últimos 150 dias do checkpoint, o gap de lucro bruto cresceu US$ 926,41.

Decomposição descritiva da diferença de receita:

- aproximadamente US$ 1.275,33, ou 64%, está associado a menos unidades vendidas;
- aproximadamente US$ 716,85, ou 36%, está associado a preço/mix;
- receita média por unidade: US$ 2,35 no controle e US$ 2,06 no Theatre.

Essa decomposição é contábil/descritiva, não um contrafactual estrutural completo.

## 11. Diagnóstico causal provisório

### 11.1 O que é fortemente sustentado pela evidência desta seed

1. **O gap não é explicado por margem.** As margens são quase iguais.
2. **O gap não é explicado pelo custo de IA.** US$ 5,30 adicionais são imateriais diante de US$ 1.133,78 de lucro bruto.
3. **O Theatre sofre com throughput e disponibilidade.** Vendeu menos, repôs menos, pediu menos e teve mais ruptura.
4. **O Theatre capturou menos preço e mix.** Fez uma mudança de preço contra cinco e usou portfólio mais estreito.
5. **O Theatre negociou demais para o retorno obtido.** Foram 13 negociações contra três, enquanto continuidade de estoque permaneceu ruim.
6. **A prudência virou subinvestimento.** Comprar menos preservou caixa no início, mas reduziu vendas e caixa futuro.
7. **A resiliência prometida não se materializou.** O Theatre teve quatro falhas e US$ 34,01 de perdas, contra uma falha e US$ 3,25 no controle.

### 11.2 Falha do ciclo Crítico → Roteirista → Personagem

O Crítico identificou repetidamente:

- máquina vazia com estoque parado;
- pedidos tardios;
- ruptura de produtos comprovados;
- coleta de caixa priorizada sobre carregamento da máquina;
- metas de caixa atingidas às custas de continuidade de receita;
- “late-horizon drift”.

Das 15 revisões do Crítico até o dia 189:

- 11 foram `critical`;
- 4 foram `correction_required`;
- nenhuma foi `on_track`.

Mesmo assim, a arquitetura v1 não possui:

- consequência obrigatória para `critical`;
- auditoria mecanizada de aderência;
- orçamento de ações;
- comparação previsão versus realizado;
- memória estruturada de série temporal;
- papel de Consciência capaz de interromper o ciclo;
- acesso a evidência externa.

O Roteirista passou a reiterar planos de quatro semanas com guardrails cada vez mais detalhados. Algumas condições ficaram autossabotadoras: por exemplo, só testar preço após quatro SKUs permanecerem sete dias seguidos disponíveis e reembolsos caírem abaixo de 1%. Como a operação vivia em ruptura, o teste de preço permanecia bloqueado.

### 11.3 O que ainda não pode ser afirmado

- Uma seed parcial não prova que todo Theatre é inferior.
- Não prova que múltiplos papéis sejam intrinsecamente piores.
- Não permite estimar o efeito isolado de cada prompt.
- Não demonstra que internet, especialização ou Consciência fariam o Theatre vencer.
- Não autoriza extrapolar o placar para o dia 365.

A leitura honesta é: **esta implementação Theatre v1 está perdendo principalmente por transformar crítica em burocracia, prudência em subinvestimento e planejamento em gates que não quebram o padrão operacional.**

## 12. O conceito de Theatre ainda não representado na v1

O desenho desejado para evolução inclui:

- **Crítico como mentor/empresário sênior de vending:** avalia oportunidade perdida, forecast versus realizado, velocidade de caixa, mix, preço, ruptura e risco de fornecedor;
- **Roteirista como CFO/controller e estrategista:** transforma intenção em orçamento, forecast, cobertura de estoque, agenda de pedidos, testes e alocação de capital;
- **Consciência:** papel fora do ciclo operacional que desafia premissas, detecta ótimo local e exige hipótese nova ou experimento reversível;
- **Personagem:** executa com contrato completo, limite real de ações e prestação de contas entre plano e ação;
- **pesquisa externa controlada:** evidência de mercado congelada e citada, ou ferramenta equivalente entre braços conforme a pergunta experimental.

## 13. Como auditar sem alterar o piloto

Requisitos locais: Python 3, Git e clone completo do repositório.

```bash
git clone https://github.com/Pareto-Group-BR/theatre-business-bench.git
cd theatre-business-bench

python3 -m unittest discover -s tests -v

PYTHONPATH=src python3 -m theatre_business_bench.cli verify-pair \
  --pair runs/pairs/20260829T021739Z-pair-s1201

PYTHONPATH=src python3 -m theatre_business_bench.cli pair-status \
  --pair runs/pairs/20260829T021739Z-pair-s1201

PYTHONPATH=src python3 -m theatre_business_bench.cli render-cockpit \
  --pair runs/pairs/20260829T021739Z-pair-s1201 \
  --json-out /tmp/live-cockpit-audit.json
```

O esperado em `verify-pair` para este checkpoint é `status=passed`, `errors=[]`, dia 189 nos dois braços e os replay hashes citados acima.

Não use `render-report` como se o piloto estivesse concluído: ele deve recusar pares parciais.

## 14. Como retomar o piloto v1 canônico

A retomada real depende do transporte OpenClaw/Codex OAuth configurado para o agente `business-bench`. Outra IA, fora desse ambiente, pode auditar os arquivos mas não deve fingir que reproduziu chamadas do mesmo modelo/provedor.

No ambiente canônico:

```bash
./scripts/run-pilot-batch.sh
```

Ou, de forma explícita:

```bash
PYTHONPATH=src python3 -m theatre_business_bench.cli pair-batch \
  --pair runs/pairs/20260829T021739Z-pair-s1201
```

Não passe orçamento artificial. O default atual continua até:

- o par completar; ou
- o provedor reportar limite/rate limit real.

Depois que o runner liberar a trava:

```bash
./scripts/publish-live-cockpit-if-idle.sh
```

Nunca inicie dois runners sobre o mesmo pair. Nunca edite `state.json`, `turns.jsonl`, `model-decisions.jsonl` ou `usage.jsonl` manualmente.

## 15. Como experimentar Theatre v2 sem contaminar a v1

Há duas etapas diferentes.

### 15.1 Fork exploratório a partir do dia 189

Objetivo: testar se uma intervenção muda a **inclinação futura** a partir do mesmo estado, sem alegar que apagou a desvantagem histórica.

Desenho recomendado:

1. preservar o pair v1 intocado;
2. clonar o checkpoint Theatre do dia 189 para dois novos braços não oficiais;
3. braço A continua com Theatre v1;
4. braço B recebe Theatre v2/Consciência;
5. usar o mesmo estado inicial e o mesmo mundo determinístico futuro;
6. pré-registrar prompts, ferramentas, modelo, thinking, horizonte e métricas;
7. medir, por exemplo, os próximos 60 ou 90 dias;
8. comparar incremento de lucro bruto, ruptura, receita/unidade, variedade, ações e capital empregado;
9. registrar qualquer intervenção humana literalmente.

Se Ramon atuar como Consciência, nomear honestamente o braço como **human-assisted exploratório**. Como ele já viu o controle, não é uma intervenção cega.

### 15.2 Corrida autônoma limpa desde o dia 0

Depois de traduzir os insights do fork para regras autônomas:

1. congelar Theatre v2;
2. escolher seeds novas e pré-registradas;
3. rodar controle, v1 e v2 com paridade adequada;
4. nunca usar resultados de um braço como contexto privado do outro;
5. manter o mesmo modelo e score;
6. publicar cada par antes da agregação;
7. declarar vitória apenas pelo score econômico final, não pela qualidade narrativa.

## 16. Pesquisa externa e internet

Pesquisa pode romper ótimo local, mas muda a pergunta experimental.

### Para isolar arquitetura

- preparar antes do teste um corpus congelado de boas práticas de vending;
- registrar URLs, conteúdo, data e hash;
- disponibilizar o mesmo corpus ao controle e ao Theatre;
- medir apenas o efeito da organização dos papéis.

### Para testar o produto Theatre completo

- permitir busca ao papel de Consciência ou Roteirista;
- registrar consultas, resultados, citações e tokens;
- chamar o tratamento de **Theatre + pesquisa externa**;
- não atribuir toda a diferença à separação de papéis.

## 17. Especificação inicial sugerida para Theatre v2

### Crítico — mentor de negócio

Deve produzir:

- diagnóstico do principal gargalo econômico;
- forecast anterior versus realizado;
- custo de oportunidade quantificado;
- contradições entre plano e execução;
- uma correção obrigatória quando o status for crítico;
- critérios para verificar se a correção funcionou.

### Roteirista — CFO/controller/estrategista

Deve produzir:

- orçamento de capital para o horizonte;
- forecast de demanda e caixa;
- cobertura-alvo e ponto de pedido por SKU;
- calendário de pedidos que respeite lead time;
- política de preço e experimentos;
- portfólio atual, apostas e itens a descontinuar;
- capacidade de ação necessária versus limite real;
- gatilhos claros para escalada à Consciência.

### Consciência — ruptura estratégica

Deve responder:

- qual premissa pode estar errada;
- qual ótimo local está prendendo o sistema;
- quais três hipóteses alternativas explicam o resultado;
- qual experimento reversível tem maior valor de informação;
- qual insight externo é necessário;
- qual regra deve ser removida, mantida ou criada;
- quando devolver o controle ao ciclo normal.

### Personagem — execução auditável

Deve:

- ver explicitamente o limite de 14 ações;
- declarar a capacidade de ação usada e não usada;
- mapear cada ação a uma prioridade do plano;
- justificar desvios com fatos do estado;
- carregar estoque entregue antes de ações meramente financeiras quando isso tiver maior valor econômico;
- antecipar pedidos conforme demanda e lead time;
- devolver memória curta, factual e verificável.

## 18. Perguntas abertas para a próxima IA

1. Quanto do gap seria reduzido apenas mostrando `max_actions_per_turn=14`?
2. Quanto vem da separação de papéis e quanto vem dos prompts v1 específicos?
3. Um veredito crítico vinculante melhora a execução ou cria oscilação excessiva?
4. Qual memória estruturada mínima evita repetir diagnósticos?
5. Qual horizonte de planejamento equilibra operação de três dias e estratégia anual?
6. Quais gates de preço protegem contra refunds sem bloquear aprendizagem?
7. Qual política de variedade maximiza lucro por slot e por dólar de capital?
8. Como usar pesquisa externa sem introduzir assimetria informacional?
9. A Consciência deve rodar periodicamente, por gatilho ou pelos dois?
10. Qual experimento causal mínimo separa o efeito de cada mudança da v2?

## 19. Prompt pronto para colar em outra IA

```text
Você recebeu o handoff do Theatre Business Bench. Primeiro leia o documento inteiro. Se tiver acesso ao repositório, leia AGENTS.md, README.md, docs/ARCHITECTURE.md, docs/EXPERIMENT_PROTOCOL.md, scenarios/vending_v1.json, os quatro prompts e o checkpoint da seed 1201.

Sua missão inicial é auditar e compreender, não alterar. Confirme o estado por verify-pair e diferencie:
1) fatos verificados do checkpoint;
2) mecanismos observados nesta seed;
3) hipóteses causais ainda não identificadas;
4) mudanças propostas para Theatre v2.

Não declare vencedor antes do dia 365. Não altere nem continue o pair v1 sem o transporte canônico e autorização explícita. Não use o placar do controle como contexto para orientar o Theatre v1. Não reescreva evidência. Se desenhar um fork ou v2, crie um experimento novo, pré-registrado e claramente não oficial.

Comece produzindo:
- uma auditoria de integridade;
- um mapa causal do gap em lucro;
- três intervenções v2 priorizadas por impacto e poder de identificação;
- o desenho do menor experimento justo capaz de testá-las;
- riscos de contaminação e critérios de parada.

Use linguagem econômica e operacional. Código e arquitetura são evidência secundária. Toda conclusão deve apontar qual arquivo ou métrica a sustenta.
```

## 20. Critério de conclusão para quem continuar

Uma continuação é considerada correta somente quando:

- preserva o piloto v1;
- torna explícito se está auditando, retomando ou criando v2;
- verifica o replay antes de usar números;
- mantém paridade ou declara a assimetria experimental;
- mede resultado econômico, não eloquência dos agentes;
- registra tokens e falhas;
- publica estado parcial como parcial;
- produz uma experiência ou evidência que outra pessoa consiga reproduzir.
