import discord
from discord.ext import commands, tasks
import random
import os
import asyncio
from datetime import datetime
import json
import requests
from dotenv import load_dotenv

# Carregar variáveis de ambiente
load_dotenv()

# Configurar intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# Criar bot com prefix '!'
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

linhas_arquivo = []
CONFIG_FILE = "config_servidores.json"
ARQUIVO_DESATUALIZADO = "apelidos_dixa_desatualizado.txt"

# ID do Google Docs - carregado de variável de ambiente
GOOGLE_DOCS_FILE_ID = os.getenv("GOOGLE_DOCS_FILE_ID", "SEU_ID_AQUI")


def carregar_configs():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"❌ Erro ao carregar configurações: {e}")
    return {}


def ler_linhas_arquivo(caminho: str) -> list:
    """Lê um .txt local e devolve lista de linhas limpas."""
    if not os.path.exists(caminho):
        return []
    with open(caminho, "r", encoding="utf-8") as f:
        return [
            linha.rstrip("\n").rstrip("\r") for linha in f.readlines() if linha.strip()
        ]


def sincronizar_arquivos(conteudo: str):
    """
    Salva o conteúdo do Google Docs no arquivo desatualizado (snapshot).
    Chamado após a mensagem da meia-noite para atualizar o snapshot.
    """
    with open(ARQUIVO_DESATUALIZADO, "w", encoding="utf-8") as f:
        f.write(conteudo)

    print("🔄 Snapshot sincronizado: Docs → desatualizado.txt")


def baixar_google_docs(doc_id: str) -> str:
    """
    Baixa o conteúdo de um Google Docs como texto puro.
    """
    url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"
    response = requests.get(url, timeout=15)

    if response.status_code == 403:
        raise ValueError(
            "Acesso negado ao Google Docs (403).\n"
            "Verifique se o documento está com permissão "
            "'Qualquer pessoa com o link pode ver'."
        )

    response.raise_for_status()
    return response.text


def parse_categorias(linhas: list) -> dict:
    """
    Lê a lista de linhas e devolve um dicionário:
    { "apelido": "categoria" }
    """
    resultado = {}
    categoria_atual = None

    for linha in linhas:
        linha_limpa = linha.strip()
        if not linha_limpa:
            continue
        if linha_limpa.startswith("-"):
            categoria_atual = linha_limpa[1:].strip()
        elif categoria_atual:
            resultado[linha_limpa] = categoria_atual

    return resultado


@tasks.loop(hours=24)
async def verificar_novos_apelidos():
    """
    A cada 24h:
    1. Baixa o Google Docs
    2. Compara com ARQUIVO_DESATUALIZADO (snapshot anterior)
    3. Notifica o canal sobre apelidos novos
    """
    global linhas_arquivo

    if GOOGLE_DOCS_FILE_ID == "SEU_ID_AQUI":
        return

    try:
        conteudo = await asyncio.get_event_loop().run_in_executor(
            None, baixar_google_docs, GOOGLE_DOCS_FILE_ID
        )

        linhas_docs = [
            l.rstrip("\n").rstrip("\r") for l in conteudo.split("\n") if l.strip()
        ]
        linhas_antigas = ler_linhas_arquivo(ARQUIVO_DESATUALIZADO)

        apelidos_docs = parse_categorias(linhas_docs)
        apelidos_antigos = parse_categorias(linhas_antigas)

        adicionados = {
            apelido: categoria
            for apelido, categoria in apelidos_docs.items()
            if apelido not in apelidos_antigos
        }

        linhas_arquivo = linhas_docs

        if adicionados:
            print(f"🆕 {len(adicionados)} novo(s) apelido(s) detectado(s)!")

            configs = carregar_configs()
            for config in configs.values():
                canal_id = config.get("canal_meia_noite")
                if canal_id is None:
                    continue

                canal = bot.get_channel(canal_id)
                if canal is None:
                    continue

                por_categoria: dict = {}
                for apelido, categoria in adicionados.items():
                    por_categoria.setdefault(categoria, []).append(apelido)

                embed = discord.Embed(
                    title=f"🆕 {len(adicionados)} Novo(s) Apelido(s) Adicionado(s)!",
                    color=discord.Color.teal(),
                )
                for categoria, apelidos_lista in por_categoria.items():
                    embed.add_field(
                        name=f"📂 {categoria}",
                        value="\n".join(f"• **{a}**" for a in apelidos_lista),
                        inline=False,
                    )
                embed.set_footer(text="Adicionado via Google Docs")
                await canal.send(embed=embed)

        else:
            print("🔄 Verificação concluída — nenhum apelido novo encontrado.")

    except Exception as e:
        print(f"❌ Erro ao verificar novos apelidos: {e}")


@verificar_novos_apelidos.before_loop
async def antes_de_verificar():
    """Espera o bot estar pronto antes da primeira verificação."""
    await bot.wait_until_ready()
    await asyncio.sleep(5)


@bot.event
async def on_ready():
    global linhas_arquivo

    print(f"✅ {bot.user} conectado com sucesso!")

    if GOOGLE_DOCS_FILE_ID != "SEU_ID_AQUI":
        try:
            print("📥 Baixando apelidos do Google Docs...")

            conteudo = await asyncio.get_event_loop().run_in_executor(
                None, baixar_google_docs, GOOGLE_DOCS_FILE_ID
            )

            linhas_arquivo = [
                l.rstrip("\n").rstrip("\r") for l in conteudo.split("\n") if l.strip()
            ]

            if not os.path.exists(ARQUIVO_DESATUALIZADO):
                sincronizar_arquivos(conteudo)
                print("📋 Primeira execução: snapshot inicial criado.")

            print(
                f"✅ Arquivo carregado do Google Docs! ({len(linhas_arquivo)} linhas, {len(parse_categorias(linhas_arquivo))} apelidos)"
            )

        except Exception as e:
            print(f"❌ Erro ao baixar do Google Docs: {e}")
            print("⚠️  Tentando carregar arquivo local...")
            carregar_local()
    else:
        print("⚠️  Google Docs ID não configurado. Carregando arquivo local...")
        carregar_local()

    enviar_meia_noite.start()
    verificar_novos_apelidos.start()


def carregar_local():
    global linhas_arquivo
    arquivo_padrao = "apelidos_dixa.txt"

    if os.path.exists(arquivo_padrao):
        try:
            with open(arquivo_padrao, "r", encoding="utf-8") as f:
                linhas_arquivo = [
                    linha.rstrip("\n").rstrip("\r") for linha in f.readlines()
                ]
            print(
                f'✅ Arquivo local "{arquivo_padrao}" carregado! ({len(linhas_arquivo)} linhas)'
            )
        except Exception as e:
            print(f"❌ Erro ao carregar arquivo local: {e}")
    else:
        print(
            f'⚠️  Arquivo "{arquivo_padrao}" não encontrado. Use !carregar para carregar manualmente.'
        )


@tasks.loop(minutes=1)
async def enviar_meia_noite():
    agora = datetime.now()

    if agora.hour == 0 and agora.minute == 0:
        configs = carregar_configs()

        for id_servidor_str, config in configs.items():
            id_servidor = int(id_servidor_str)
            canal_meia_noite = config.get("canal_meia_noite")
            usuario_apelido = config.get("usuario_apelido")

            apelido_do_dia = None

            if canal_meia_noite is not None:
                canal = bot.get_channel(canal_meia_noite)

                if canal is not None and linhas_arquivo:
                    linhas_validas = [
                        linha.strip()
                        for linha in linhas_arquivo
                        if linha.strip() and not linha.strip().startswith("-")
                    ]

                    if linhas_validas:
                        apelido_do_dia = random.choice(linhas_validas)

                        embed = discord.Embed(
                            title="🌙 Mensagem da Meia-Noite",
                            description=f"🎉 O apelido do dia é: **{apelido_do_dia}**",
                            color=discord.Color.dark_blue(),
                        )
                        embed.set_footer(
                            text=f"Enviado às {agora.strftime('%H:%M:%S')}"
                        )

                        await canal.send(embed=embed)

            if usuario_apelido is not None and apelido_do_dia is not None:
                try:
                    servidor = bot.get_guild(id_servidor)
                    if servidor is not None:
                        membro = servidor.get_member(usuario_apelido)

                        if membro is not None:
                            nome_anterior = membro.nick if membro.nick else membro.name

                            await membro.edit(nick=apelido_do_dia)

                            canal = bot.get_channel(canal_meia_noite)
                            if canal is not None:
                                await canal.send(
                                    f"✅ O apelido de **{nome_anterior}** (apelido do dia anterior), foi alterado para: **{apelido_do_dia}**. {membro.mention}"
                                )

                            print(
                                f"✅ Apelido de {nome_anterior} alterado para: {apelido_do_dia}"
                            )

                except Exception as e:
                    print(f"❌ Erro ao alterar apelido: {e}")

        try:
            conteudo_atual = await asyncio.get_event_loop().run_in_executor(
                None, baixar_google_docs, GOOGLE_DOCS_FILE_ID
            )
            sincronizar_arquivos(conteudo_atual)
        except Exception as e:
            print(f"❌ Erro ao sincronizar snapshot: {e}")

        await asyncio.sleep(61)


@bot.command(name="recarregar")
@commands.has_permissions(administrator=True)
async def recarregar_drive(ctx):
    """Recarrega o arquivo do Google Docs manualmente (Admin)."""
    global linhas_arquivo

    await ctx.send("📥 Baixando arquivo do Google Docs...")

    try:
        conteudo = await asyncio.get_event_loop().run_in_executor(
            None, baixar_google_docs, GOOGLE_DOCS_FILE_ID
        )

        linhas_arquivo = [
            linha.rstrip("\n").rstrip("\r")
            for linha in conteudo.split("\n")
            if linha.strip()
        ]

        await ctx.send(
            f"✅ Arquivo recarregado com sucesso! ({len(linhas_arquivo)} linhas)"
        )

    except Exception as e:
        await ctx.send(f"❌ Erro ao baixar do Google Docs:\n```{e}```")


@bot.command(name="apelido")
async def enviar_apelido(ctx):

    if not linhas_arquivo:
        await ctx.send(
            "❌ Nenhum arquivo foi carregado! Use !carregar arquivo.txt primeiro."
        )
        return

    categorias = {}
    categoria_atual = None

    for linha in linhas_arquivo:
        linha_limpa = linha.strip()

        if linha_limpa.startswith("-"):
            categoria_atual = linha_limpa[1:].strip()
            if categoria_atual not in categorias:
                categorias[categoria_atual] = []

        elif categoria_atual and linha_limpa:
            categorias[categoria_atual].append(linha_limpa)

    todas_mensagens = []

    for categoria, itens in categorias.items():
        for item in itens:
            todas_mensagens.append({"mensagem": item, "categoria": categoria})

    if not todas_mensagens:
        await ctx.send("❌ Nenhuma mensagem válida encontrada no arquivo!")
        return

    escolhida = random.choice(todas_mensagens)

    embed = discord.Embed(
        title="✨ Apelido Aleatório",
        description=escolhida["mensagem"],
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="📂 Categoria",
        value=escolhida["categoria"],
        inline=False,
    )

    await ctx.send(embed=embed)


@bot.command(name="quais", aliases=["categorias"])
async def quais_categorias_tem(ctx, *args):

    if not linhas_arquivo:
        await ctx.send(
            "❌ Nenhum arquivo foi carregado! Use !carregar arquivo.txt primeiro."
        )
        return

    categorias = {}
    categoria_atual = None

    for linha in linhas_arquivo:
        linha_limpa = linha.strip()

        if linha_limpa.startswith("-"):
            categoria_atual = linha_limpa[1:].strip()
            if categoria_atual not in categorias:
                categorias[categoria_atual] = []

        elif categoria_atual and linha_limpa:
            categorias[categoria_atual].append(linha_limpa)

    if not categorias:
        await ctx.send("❌ Nenhuma categoria encontrada no arquivo!")
        return

    embed = discord.Embed(title="📂 CATEGORIAS", color=discord.Color.green())

    for categoria, apelidos in categorias.items():
        embed.add_field(
            name=categoria,
            value=f"**{len(apelidos)}** apelidos",
            inline=False,
        )

    await ctx.send(embed=embed)


class PaginadorApelidos(discord.ui.View):
    """View com botões ← → para navegar entre categorias."""

    def __init__(self, categorias: dict, autor_id: int):
        super().__init__(timeout=60)
        self.categorias = list(categorias.items())
        self.autor_id = autor_id
        self.pagina = 0

    def embed_atual(self) -> discord.Embed:
        categoria, apelidos = self.categorias[self.pagina]
        embed = discord.Embed(
            title=f"📂 {categoria}",
            description="\n".join(f"• {a}" for a in apelidos),
            color=discord.Color.blue(),
        )
        embed.set_footer(
            text=f"Categoria {self.pagina + 1} de {len(self.categorias)} • {len(apelidos)} apelidos"
        )
        return embed

    def atualizar_botoes(self):
        self.btn_anterior.disabled = self.pagina == 0
        self.btn_proximo.disabled = self.pagina == len(self.categorias) - 1

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def btn_anterior(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message(
                "❌ Só quem usou o comando pode navegar!", ephemeral=True
            )
            return
        self.pagina -= 1
        self.atualizar_botoes()
        await interaction.response.edit_message(embed=self.embed_atual(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def btn_proximo(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message(
                "❌ Só quem usou o comando pode navegar!", ephemeral=True
            )
            return
        self.pagina += 1
        self.atualizar_botoes()
        await interaction.response.edit_message(embed=self.embed_atual(), view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


@bot.command(name="todos")
async def todos_apelidos(ctx, *args):

    if not linhas_arquivo:
        await ctx.send("❌ Nenhum arquivo carregado!")
        return

    categorias = {}
    categoria_atual = None

    for linha in linhas_arquivo:
        linha_limpa = linha.strip()
        if linha_limpa.startswith("-"):
            categoria_atual = linha_limpa[1:].strip()
            if categoria_atual not in categorias:
                categorias[categoria_atual] = []
        elif categoria_atual and linha_limpa:
            categorias[categoria_atual].append(linha_limpa)

    if not categorias:
        await ctx.send("❌ Nenhuma categoria encontrada no arquivo!")
        return

    view = PaginadorApelidos(categorias, ctx.author.id)
    view.atualizar_botoes()
    await ctx.send(embed=view.embed_atual(), view=view)


@bot.command(name="silabas")
async def gerar_apelido(ctx):

    prefixos = [
        "telé",
        "pim",
        "wing",
        "ban",
        "splink",
        "hatio",
        "tchon",
        "tché",
        "ton",
        "xilé",
        "bu",
        "trel",
        "tchog",
        "chu",
        "bog",
        "cam",
        "top",
        "esn",
        "plog",
        "cop",
        "smô",
        "glo",
    ]
    meios = [
        "bo",
        "ber",
        "slom",
        "de",
        "stron",
        "flin",
        "vis",
        "go",
        "lé",
        "gas",
        "le",
        "gle",
        "pis",
        "ban",
        "pó",
        "nó",
        "órn",
        "ólp",
        "glers",
    ]
    sufixos = [
        "gas",
        "tos",
        "rox",
        "blus",
        "bum",
        "pito",
        "nlu",
        "lay",
        "pson",
        "clay",
        "klins",
        "fly",
        "flay",
        "vis",
        "mongo",
        "pisz",
        "bugas",
        "borg",
        "glets",
        "quets",
        "lers",
    ]

    num_silabas = random.choice([2, 3])

    if num_silabas == 2:
        apelido = random.choice(prefixos) + random.choice(sufixos)
    else:
        apelido = (
            random.choice(prefixos) + random.choice(meios) + random.choice(sufixos)
        )

    apelido = apelido.capitalize()

    embed = discord.Embed(
        title="✨ Seu Apelido Aleatório",
        description=apelido,
        color=discord.Color.purple(),
    )

    await ctx.send(embed=embed)


SILABAS_SUPER = [
    "dri",
    "klo",
    "ver",
    "son",
    "as",
    "preu",
    "ble",
    "tor",
    "mas",
    "que",
    "mi",
    "guar",
    "dis",
    "in",
    "sla",
    "psa",
    "vo",
    "klan",
    "ni",
    "mõn",
    "gdon",
    "drio",
    "joer",
    "nog",
    "los",
    "sono",
    "õg",
    "nor",
    "dras",
    "le",
    "pur",
    "min",
    "xô",
    "mig",
    "ñor",
    "blis",
    "ter",
    "glov",
    "im",
    "dir",
    "ner",
    "xa",
    "mur",
    "lin",
    "plo",
    "pler",
    "nior",
]


def _gerar_palavra_super() -> str:
    tamanho = random.randint(3, 6)
    return "".join(random.choice(SILABAS_SUPER) for _ in range(tamanho))


@bot.command(name="super-silaba")
async def gerar_super_apelido(ctx):

    palavra1 = _gerar_palavra_super()
    palavra2 = _gerar_palavra_super()
    apelido = f"{palavra1} {palavra2}".capitalize()

    embed = discord.Embed(
        title="🤯 Super Apelido",
        description=f"**{apelido}**",
        color=discord.Color.og_blurple(),
    )

    await ctx.send(embed=embed)


@bot.command(name="teste")
async def teste_meia_noite(ctx):

    if not ctx.author.guild_permissions.administrator:
        await ctx.send(
            "❌ Você precisa ser administrador do servidor para usar este comando!"
        )
        return

    if not linhas_arquivo:
        await ctx.send(
            "❌ Nenhum arquivo foi carregado! Use !carregar arquivo.txt primeiro."
        )
        return

    try:
        configs = carregar_configs()
        config = configs.get(str(ctx.guild.id), {})

        canal_meia_noite = config.get("canal_meia_noite")
        usuario_apelido = config.get("usuario_apelido")

        if canal_meia_noite is not None:
            canal = bot.get_channel(canal_meia_noite)

            if canal is not None:
                linhas_validas = [
                    linha.strip()
                    for linha in linhas_arquivo
                    if linha.strip() and not linha.strip().startswith("-")
                ]

                if linhas_validas:
                    apelido_do_dia = random.choice(linhas_validas)

                    embed = discord.Embed(
                        title="🌙 Teste forçado de alteração de apelido às 00:00",
                        description=f"🎉 O apelido do dia é: **{apelido_do_dia}**",
                        color=discord.Color.dark_blue(),
                    )
                    await canal.send(embed=embed)

                    if usuario_apelido is not None:
                        servidor = bot.get_guild(ctx.guild.id)

                        if servidor is not None:
                            membro = servidor.get_member(usuario_apelido)

                            if membro is not None:
                                nome_anterior = (
                                    membro.nick if membro.nick else membro.name
                                )

                                await membro.edit(nick=apelido_do_dia)

                                await canal.send(
                                    f"✅ O apelido de **{nome_anterior}** (apelido do dia anterior), foi alterado para: **{apelido_do_dia}**. {membro.mention}"
                                )

    except Exception as e:
        await ctx.send(f"❌ Erro ao testar: {e}")


@bot.command(name="testenovo")
@commands.has_permissions(administrator=True)
async def teste_novos_apelidos(ctx):
    """Força a verificação de novos apelidos agora (Admin)."""
    await ctx.send("🔄 Verificando novos apelidos no Google Docs...")
    await verificar_novos_apelidos()
    await ctx.send("✅ Verificação concluída! Veja o canal da meia-noite.")


@bot.command(name="ajuda", aliases=["comandos", "help"])
async def ajuda(ctx, *args):
    embed = discord.Embed(
        title="📖 Comandos do Bot",
        description="Lista de comandos disponíveis:",
        color=discord.Color.blue(),
    )
    embed.add_field(
        name="!recarregar",
        value="Recarrega o arquivo do Google Docs (Admin)",
        inline=False,
    )
    embed.add_field(
        name="!apelido",
        value="Envia um apelido aleatório do arquivo carregado com categoria",
        inline=False,
    )
    embed.add_field(
        name="!todos",
        value="Mostra todos os apelidos do arquivo separados por categoria",
        inline=False,
    )
    embed.add_field(
        name="!quais / !categorias",
        value="Mostra todas as categorias com a quantidade de apelidos",
        inline=False,
    )
    embed.add_field(
        name="!silabas",
        value="Gera um apelido aleatório com sílabas",
        inline=False,
    )
    embed.add_field(
        name="!super-silaba",
        value="Gera um super apelido com duas palavras malucas",
        inline=False,
    )
    embed.add_field(
        name="!teste",
        value="Testa a função de meia-noite (Admin)",
        inline=False,
    )
    embed.add_field(
        name="!testenovo",
        value="Força verificação de novos apelidos no Docs agora (Admin)",
        inline=False,
    )
    embed.add_field(
        name="!ajuda / !comandos / !help",
        value="Mostra esta mensagem",
        inline=False,
    )
    await ctx.send(embed=embed)


# Token do Discord
token = os.getenv("DISCORD_TOKEN")

if not token:
    print("❌ DISCORD_TOKEN não configurado!")
    exit()

bot.run(token)
