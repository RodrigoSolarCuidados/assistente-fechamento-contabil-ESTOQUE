# Automação do Fechamento Contábil de Estoque (IW → MXM)

## Contexto

A Solar controla fisicamente o estoque (Farmácia/Dietas/Mat. Enfermagem/Materiais Diversos) no sistema **IW**, que integra as NFs de entrada via API para o **MXM** (ERP contábil), lançando-as a débito na conta transitória **115010005**. Ao final do mês, a Contabilidade usa o **relatório de Fechamento de Estoque do IW** para lançar manualmente, por filial e grupo de item, os créditos que destrincham essa conta transitória nas 4 contas de ativo (115010001 Medicamentos, 115010002 Material Hospitalar, 115010003 Dietas, 115010004 Materiais Diversos), além de registrar Consumo, Movimento, Ajuste e Descarte com suas contrapartidas de despesa.

Esse processo é manual e sujeito a classes de erro que hoje só aparecem quando a conta transitória não zera no fechamento, ou pior, passam despercebidas:
1. **NFs que existem no IW mas não integraram no MXM** (fornecedor corre risco de não pagamento — precisa ir para o TI).
2. **NFs/lançamentos que existem no MXM mas não deveriam estar lá** (ex.: notas de serviço lançadas incorretamente na conta de estoque) ou que existem no MXM sem nota correspondente no IW.
3. **NFs canceladas no IW que, por algum erro de processo, têm lançamento correspondente no MXM** — risco grave de pagamento indevido a fornecedor.
4. **NFs de Bonificação**, que teoricamente não integram ao MXM automaticamente e precisam de lançamento contábil próprio, hoje dependente de identificação manual.

O pedido é automatizar duas coisas: (1) a geração dos lançamentos contábeis propostos do fechamento mensal a partir do relatório do IW (incluindo bonificações), e (2) a conciliação IW × MXM para expor essas divergências — incluindo o caso crítico de cancelamento — antes do fechamento, reduzindo o trabalho manual e o risco de erro/atraso de pagamento a fornecedor.

A estrutura real dos 3 arquivos de exemplo (Agosto/2026) presentes na pasta do projeto foi inspecionada e validada com o usuário, revelando um ponto estrutural importante que muda o desenho original: **os 3 arquivos são cumulativos (Jan–Ago/2026), não recortes do mês** — todo mês o usuário reexporta o histórico inteiro, não apenas o período corrente.

## Arquivos de entrada (exemplo validado: Agosto/2026)

Os nomes de arquivo **não são confiáveis para descoberta** (erros de digitação históricos, e como os arquivos agora são cumulativos, o nome do mês nem reflete mais o conteúdo). A pasta de trabalho contém exatamente **1 arquivo de cada tipo**; o script identifica o tipo pela estrutura de colunas, não pelo nome.

- **NFs do IW** (`.csv`, delimitador `;`, decimal `,`, encoding ANSI/Latin-1). Colunas: `EMPRESA; TIPONF; STATUS; DATALANCTO; DATANOTA; ID; CNPJ; FULLNAME; NRNF; BSSERIAL; DATAVENTO; VALOR; FRETE; VLRNF`. Confirmado no arquivo de exemplo (9.429 linhas, `DATALANCTO` de 2026-01 a 2026-08 — cumulativo):
  - `EMPRESA`: `"SOLAR"` (filial RJ) ou `"SOLAR SP"` (filial SP).
  - `STATUS`: só dois valores — `ATIVA` (9.303 linhas) e `CANCELADA` (66 linhas).
  - `TIPONF`: três valores — `Nota Fiscal` (9.361), `Bonificação` (6), `Nota Pedido` (2, tratada igual a Nota Fiscal).
- **Fechamento Estoque** (2 arquivos `.csv`, um por filial, mesma estrutura de colunas): `FILIAL; IDLOT; PERIODO; TIPOMATERIAL; IDTABLE; SALDOINICIAL; COMPRAS; ENTRADA_VL; ENTR_VL_T0_OUTR; DEVOLCONSUMO_VL; DEVOLCOMPRAS_VL; SAIDA_VL; MOVIMENTO; AJUSTE; CONSUMO; PERDAS; SALDOPERIODO; SALDOFINAL; CANCELNFVL; SALDOFINALCALC`. Confirmado nos arquivos de exemplo — também cumulativo: RJ tem `PERIODO` de `2026/01` a `2026/08` (33 linhas, `FILIAL="SOLAR RJ"`), SP tem `2026/04` a `2026/08` (21 linhas, `FILIAL="SOLAR SP"`). `PERIODO` no formato `YYYY/MM` é o campo usado para filtrar o mês corrente (`--mes`) antes de aplicar as regras de lançamento.
- **Razão Contábil Estoque MXM** (`.xlsx`, via `openpyxl`, ~9.500 linhas/mês, também cumulativo). Colunas relevantes: A=Cód. Empresa (`01`=RJ, `06`=SP), C=Nome do Fornecedor, D=Desc. Conta Contábil, E=Conta Contábil, I=Valor, J=Valor Débito, K=Valor Crédito, L=Data Lançamento, M=Lote, N=Documento (id interno, não é o nº da NF), P=Histórico, S=No.Título. O **nº da NF não tem coluna própria**, mas está no início de `No.Título` (ex.: `000054297.63927.1` → NF `000054297`) — extraído fazendo `split('.')` e pegando o primeiro segmento, convertido para inteiro (remove zeros à esquerda automaticamente). **Não é necessário regex em `Histórico`, nem extrair CNPJ** (CNPJ só existe no lado MXM, não serve para casar com o IW). Lotes encontrados: `SCP_PR`, `SCP_BX`, `SGP_PR` (lançamentos de integração automática IW→MXM) e `CMV`, `CONTAB` (lançamentos manuais já existentes, inclusive fechamentos de meses anteriores — não entram na conciliação).

### De-para de filial confirmado entre as 3 fontes
| Filial | NFs IW (`EMPRESA`) | Fechamento (`FILIAL`) | MXM (`Cód. Empresa`) |
|---|---|---|---|
| RJ | `SOLAR` | `SOLAR RJ` | `01` |
| SP | `SOLAR SP` | `SOLAR SP` | `06` |

## Regras de negócio confirmadas

**Contas contábeis (Ativo):**
| Conta | Grupo |
|---|---|
| 115010001 | Medicamentos |
| 115010002 | Material Hospitalar (= "Mat. Enfermagem" no IW) |
| 115010003 | Dietas |
| 115010004 | Materiais Diversos |
| 115010005 | Conta Transitória |

**De-para TIPOMATERIAL (Fechamento IW) → conta:** Dietas→115010003, Mat. Enfermagem→115010002, Materiais Diversos→115010004, Medicamentos→115010001.

**Lançamentos de fechamento, por filial/grupo, a partir das colunas do relatório de Fechamento (filtrado por `PERIODO == --mes`):**
| Movimento | Coluna no Fechamento | Débito | Crédito |
|---|---|---|---|
| Compras | `COMPRAS` | Conta do grupo (11501000X) | 115010005 (Transitória) |
| Consumo | `CONSUMO` | 411010002 (Despesa) | Conta do grupo |
| Movimento | `MOVIMENTO` | 411060001 (Despesa) | Conta do grupo |
| Ajuste | `AJUSTE` | 411060001 | Conta do grupo |
| Descarte | `PERDAS` | 411060001 | Conta do grupo |

As demais colunas do Fechamento (`ENTRADA_VL`, `ENTR_VL_T0_OUTR`, `DEVOLCONSUMO_VL`, `DEVOLCOMPRAS_VL`, `SAIDA_VL`, `CANCELNFVL`, `SALDOFINALCALC`) são informativas/de conferência interna do IW e não geram lançamento próprio. Quando o valor de uma coluna de movimento vier negativo, o script inverte Débito/Crédito daquela linha (mantendo o valor em módulo), em vez de lançar valor negativo.

**Classificação de cada NF do IW (ordem de precedência), para fins de lançamento e conciliação:**
1. **`TIPONF == "Bonificação"`** → nunca entra na conciliação normal nem na crítica de cancelamento, independente de `STATUS`. Gera lançamento próprio (ver abaixo).
2. **`STATUS == "CANCELADA"`** (e não é Bonificação):
   - Se casar com um lançamento no MXM (mesma chave de conciliação) → vai para a aba crítica de risco de pagamento indevido.
   - Se não casar → comportamento esperado/correto (nota cancelada e corretamente não integrada) — **não aparece em nenhuma aba**.
3. **`STATUS == "ATIVA"`** (inclui `TIPONF` = "Nota Fiscal" ou "Nota Pedido", tratados de forma idêntica) → entra na conciliação normal contra o MXM.

**Lançamento de Bonificação:** NFs de `TIPONF == "Bonificação"` teoricamente não integram automaticamente ao MXM. O script gera **um lançamento por NF** (não agregado), usando todo o histórico acumulado do arquivo (o usuário prefere ver tudo e filtrar manualmente o que já foi contabilizado):
- Débito: 115010005 (Estoque Transitório)
- Crédito: 321010003 (Receita)
- Valor: `VLRNF` (valor total da nota, incluindo frete)

**Conciliação NF (IW) × Lançamento (MXM):**
- Escopo: **todo o histórico acumulado** dos arquivos (não filtrado por `--mes`), tanto para a conciliação normal quanto para a crítica de cancelamento e para "lançamentos somente MXM" — pendências antigas continuam aparecendo até serem resolvidas, o que é o comportamento desejado (reduzir risco de não pagamento).
- Chave: `NRNF` (convertido para inteiro nos dois lados, elimina zeros à esquerda) + `Valor` (comparação **exata**, sem tolerância de centavos), escopada por filial.
  - Lado IW: coluna `VLRNF`.
  - Lado MXM: coluna `Valor` (I), apenas linhas na conta `115010005` com lote de integração automática (`SCP_PR`, `SCP_BX`, `SGP_PR`).
- **Duplicidade**: se a combinação NRNF+Valor aparecer mais de uma vez do mesmo lado (mesma filial), não tenta casar automaticamente — cai nas abas de exceção já previstas, com uma coluna extra sinalizando "chave duplicada — revisar manualmente".

**Saldo da Transitória (por filial), para conferência:**
- **Saldo antes**: saldo contábil real da conta 115010005 no Razão MXM (débitos − créditos das linhas até o fim do mês de `--mes`, por `Data Lançamento`).
- **Saldo depois**: saldo antes − soma dos créditos das linhas "Compras" propostas na aba de Lançamentos Propostos daquela filial. Esperado: zero.

## O que será construído

Um script Python (`Estoque/gerar_fechamento.py` + módulos de apoio), executado manualmente todo mês, que lê os 3 arquivos de entrada (sempre cumulativos, 1 de cada tipo na pasta atual) e gera uma planilha Excel de saída com o fechamento proposto e as pendências de conciliação.

**CLI:** `python gerar_fechamento.py --mes 2026-08` — só o mês; a pasta de trabalho (diretório atual) é onde o script procura os 3 arquivos e onde escreve o Excel de saída (nome automático, ex. `fechamento_2026-08.xlsx`). Se algum dos 3 tipos de arquivo não for encontrado ou tiver estrutura inesperada: **abortar com mensagem de erro clara**, sem gerar planilha parcial.

**Escopo temporal por aba/lançamento:**
| Conteúdo | Escopo |
|---|---|
| Lançamentos Propostos (Compras/Consumo/Movimento/Ajuste/Descarte) | Só `--mes` (via `PERIODO` do Fechamento) |
| Lançamentos de Bonificação | Todo o histórico acumulado |
| NFs Canceladas com Lançamento MXM (crítico) | Todo o histórico acumulado |
| NFs somente IW (pendências) | Todo o histórico acumulado |
| Lançamentos somente MXM (pendências) | Todo o histórico acumulado |
| Resumo por Conta (saldo antes) | Até o fim do mês de `--mes` |

**Módulos:**
- `parsers.py`: leitura dos CSVs do IW (delimitador `;`, decimal `,`, encoding `latin-1`/`cp1252`) e do Razão Contábil MXM (`.xlsx`, via `openpyxl`). Extrai NRNF do MXM via `No.Título.split('.')[0]` → inteiro. Não extrai CNPJ. Descobre os arquivos na pasta atual pela estrutura de colunas (não pelo nome).
- `reconciliation.py`: monta a chave NRNF(int)+Valor por filial dos dois lados (todo o histórico), aplica a ordem de precedência de classificação (Bonificação → Cancelada → Ativa), e retorna: matches OK, NFs somente IW, NFs canceladas com match MXM (crítico), lançamentos somente MXM, e duplicidades sinalizadas nas abas correspondentes.
- `lancamentos.py`: aplica a tabela de regras sobre o(s) relatório(s) de Fechamento filtrado(s) por `PERIODO == --mes` (Lançamentos Propostos), e gera as linhas de Lançamentos de Bonificação (todo o histórico, uma linha por NF).
- `gerar_fechamento.py`: orquestra tudo, calcula saldo antes/depois por filial, e escreve o Excel de saída com as abas: **Lançamentos Propostos**, **Lançamentos de Bonificação**, **NFs Canceladas com Lançamento MXM**, **NFs somente IW**, **Lançamentos somente MXM**, **Resumo por Conta**.
- `requirements.txt`: `pandas`, `openpyxl`.

**Fora de escopo nesta primeira versão:** lançamento automático direto no MXM (via API/integração) — o script só gera a planilha para revisão humana, que hoje já é o fluxo de lançamento manual.

## Arquivos a criar/tocar
- `Estoque/gerar_fechamento.py` (orquestração/CLI: recebe `--mes`, lê a pasta atual)
- `Estoque/parsers.py`
- `Estoque/reconciliation.py`
- `Estoque/lancamentos.py`
- `Estoque/requirements.txt`

## Verificação
1. Rodar `pip install -r requirements.txt` e executar `python gerar_fechamento.py --mes 2026-08` usando os 3 arquivos de Agosto já presentes na pasta.
2. Conferir manualmente, para 2-3 linhas do Fechamento RJ e SP, que o lançamento proposto bate com a regra (conta, débito/crédito, valor), incluindo um caso de valor negativo (inversão de débito/crédito).
3. Conferir que o somatório de Débitos das linhas "Compras" propostas para a conta 115010005 bate com o somatório de créditos únicos vindos das NFs do IW que casaram no MXM (todo o histórico).
4. Conferir que as 6 NFs de Bonificação aparecem na aba própria (Débito 115010005/Crédito 321010003, valor = VLRNF) e não aparecem em nenhuma aba de pendência.
5. Conferir que, das 66 NFs Canceladas, as que casam com o MXM vão para a aba crítica e as que não casam não aparecem em nenhuma aba.
6. Validar com o usuário 2-3 casos reais de NF que ele já sabe que estão pendentes de integração (se houver) para confirmar que aparecem na aba "NFs somente IW".
7. Conferir que "Resumo por Conta" dá saldo depois = 0 (ou próximo, dentro do esperado) para RJ e SP.
