import discord
from discord.ext import commands, tasks
import random
import os
import asyncio
from datetime import datetime
import json
import requests
import asyncpg
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

# IDs do Google Docs
GOOGLE_DOCS_FILE_ID = os.getenv("GOOGLE_DOCS_FILE_ID", "SEU_ID_AQUI")

# Conexão com o banco
DATABASE_URL = os.getenv("DATABASE_URL")
db_pool = None


# ──────────────────────────────────────────────
# BANCO DE DADOS
# ──────────────────────────────────────────────

async def init_db():
    """Cria o pool de conexões e garante que a tabela existe."""
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS apelidos_comparativo (
                apelido TEXT PRIMARY KEY,
                categoria TEXT NOT NULL
            )
        """)
    print("✅ Banco de dados conectado e tabela verificada!")


async def carregar_comparativo() -> dict:
    """Retorna { apelido: categoria } a partir do banco."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT apelido, categoria FROM apelidos_comparativo")
    return {row["apelido"]: row["categoria"] for row in rows}


async def salvar_comparativo(apelidos: dict):
    """
    Sincroniza o banco com o dicionário recebido.
    Apaga tudo e reinsere — simples e confiável.
    """
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM apelidos_comparativo")
        if apelidos:
            await conn.executemany(
                "INSERT INTO apelidos_comparativo (apelido, categoria) VALUES ($1, $2)",
                list(apelidos.items())
            )
    print(f"✅ Comparativo salvo no banco com {len(apelidos)} apelidos.")


# ──────────────────────────────────────────────
# CONFIGS
# ──────────────────────────────────────────────

def carregar_configs():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
            print(f"✅ DEBUG: Arquivo '{CONFIG_FILE}' carregado com sucesso!")
            print(f"   📋 Total de servidores configurados: {len(config)}")
            for id_servidor, conf in config.items():
                print(f"      • Servidor {id_servidor}: canal={conf.get('canal_meia_noite')}, usuário={conf.get('usuario_apelido')}")
            return config
        except Exception as e:
            print(f"❌ Erro ao carregar configurações: {e}")
    else:
        print(f"⚠️ DEBUG: Arquivo '{CONFIG_FILE}' não encontrado!")
    return {}


# ──────────────────────────────────────────────
# GOOGLE DOCS
# ──────────────────────────────────────────────

def baixar_google_docs(doc_id: str) -> str:
    """Baixa o conteúdo de um Google Docs como texto puro."""
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


# ──────────────────────────────────────────────
# EVENTOS
# ──────────────────────────────────────────────

@bot.event
async def on_ready():
    global linhas_arquivo

    print(f"✅ {bot.user} conectado com sucesso!")

    await init_db()

    if GOOGLE_DOCS_FILE_ID != "SEU_ID_AQUI":
        try:
            print("📥 Baixando apelidos do Google Docs...")

            conteudo = await asyncio.get_event_loop().run_in_executor(
                None, baixar_google_docs, GOOGLE_DOCS_FILE_ID
            )

            linhas_arquivo = [
                l.rstrip("\n").rstrip("\r") for l in conteudo.split("\n") if l.strip()
            ]

            print(
                f"✅ Arquivo carregado do Google Docs! ({len(linhas_arquivo)} linhas, {len(parse_categorias(linhas_arquivo))} apelidos)"
            )

        except Exception as e:
            print(f"❌ Erro ao baixar do Google Docs: {e}")
    else:
        print("⚠️  Google Docs ID não configurado!")

    carregar_configs()
    enviar_meia_noite.start()


# ──────────────────────────────────────────────
# TASK PRINCIPAL — 19h Brasília (22h UTC)
# ──────────────────────────────────────────────

@tasks.loop(minutes=1)
async def enviar_meia_noite():
    agora = datetime.utcnow()

    if agora.hour == 22 and agora.minute == 0:
        configs = carregar_configs()

        # 1. Verificar novos apelidos
        apelidos_comparativo = await carregar_comparativo()
        adicionados = {}

        try:
            conteudo_principal = await asyncio.get_event_loop().run_in_executor(
                None, baixar_google_docs, GOOGLE_DOCS_FILE_ID
            )
            linhas_principal = [
                l.rstrip("\n").rstrip("\r") for l in conteudo_principal.split("\n") if l.strip()
            ]
            apelidos_principal = parse_categorias(linhas_principal)

            adicionados = {
                apelido: categoria
                for apelido, categoria in apelidos_principal.items()
                if apelido not in apelidos_comparativo
            }

            await salvar_comparativo(apelidos_principal)

        except Exception as e:
            print(f"❌ Erro ao verificar novos apelidos: {e}")

        # 2. Enviar para cada servidor
        for id_servidor_str, config in configs.items():
            id_servidor = int(id_servidor_str)
            canal_meia_noite = config.get("canal_meia_noite")
            usuario_apelido = config.get("usuario_apelido")
            apelido_do_dia = None

            if canal_meia_noite is None:
                continue

            canal = bot.get_channel(canal_meia_noite)
            if canal is None:
                continue

            # 2a. Anunciar novos apelidos (se houver)
            if adicionados:
                por_categoria = {}
                for apelido, categoria in adicionados.items():
                    por_categoria.setdefault(categoria, []).append(apelido)

                embed_novos = discord.Embed(
                    title=f"🆕 {len(adicionados)} Novo(s) Apelido(s) Adicionado(s)!",
                    color=discord.Color.teal(),
                )
                for categoria, apelidos_lista in por_categoria.items():
                    embed_novos.add_field(
                        name=f"📂 {categoria}",
                        value="\n".join(f"• **{a}**" for a in apelidos_lista),
                        inline=False,
                    )
                embed_novos.set_footer(text="Adicionado via Google Docs")
                await canal.send(embed=embed_novos)

            # 2b. Apelido do dia
            if linhas_arquivo:
                linhas_validas = [
                    linha.strip()
                    for linha in linhas_arquivo
                    if linha.strip() and not linha.strip().startswith("-")
                ]

                if linhas_validas:
                    apelido_do_dia = random.choice(linhas_validas)[:32]

                    embed_dia = discord.Embed(
                        title="🌙 Apelido do Dia",
                        description=f"🎉 O apelido do dia é: **{apelido_do_dia}**",
                        color=discord.Color.dark_blue(),
                    )
                    embed_dia.set_footer(text=f"Enviado às {agora.strftime('%H:%M:%S')} UTC")
                    await canal.send(embed=embed_dia)

            # 2c. Alterar apelido do membro
            if usuario_apelido is not None and apelido_do_dia is not None:
                try:
                    servidor = bot.get_guild(id_servidor)
                    if servidor is not None:
                        membro = servidor.get_member(usuario_apelido)
                        if membro is not None:
                            nome_anterior = membro.nick if membro.nick else membro.name
                            await membro.edit(nick=apelido_do_dia)
                            await canal.send(
                                f"✅ O apelido de **{nome_anterior}** foi alterado para: **{apelido_do_dia}**. {membro.mention}"
                            )
                            print(f"✅ Apelido de {nome_anterior} alterado para: {apelido_do_dia}")
                except Exception as e:
                    print(f"❌ Erro ao alterar apelido: {e}")

        await asyncio.sleep(61)


# ──────────────────────────────────────────────
# COMANDOS
# ──────────────────────────────────────────────

@bot.command(name="recarregar")
@commands.has_permissions(administrator=True)
async def recarregar_drive(ctx):
    """Recarrega o arquivo do Google Docs (Admin)."""
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

        await ctx.send(f"✅ Arquivo recarregado com sucesso! ({len(linhas_arquivo)} linhas)")

    except Exception as e:
        await ctx.send(f"❌ Erro ao baixar do Google Docs:\n```{e}```")


@bot.command(name="apelido")
async def enviar_apelido(ctx):

    if not linhas_arquivo:
        await ctx.send("❌ Nenhum arquivo foi carregado!")
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
        await ctx.send("❌ Nenhuma mensagem válida encontrada!")
        return

    escolhida = random.choice(todas_mensagens)

    embed = discord.Embed(
        title="✨ Apelido Aleatório",
        description=escolhida["mensagem"],
        color=discord.Color.gold(),
    )
    embed.add_field(name="📂 Categoria", value=escolhida["categoria"], inline=False)
    await ctx.send(embed=embed)


@bot.command(name="quais", aliases=["categorias"])
async def quais_categorias_tem(ctx, *args):

    if not linhas_arquivo:
        await ctx.send("❌ Nenhum arquivo foi carregado!")
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
        await ctx.send("❌ Nenhuma categoria encontrada!")
        return

    embed = discord.Embed(title="📂 CATEGORIAS", color=discord.Color.green())
    for categoria, apelidos in categorias.items():
        embed.add_field(name=categoria, value=f"**{len(apelidos)}** apelidos", inline=False)
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
    async def btn_anterior(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message("❌ Só quem usou o comando pode navegar!", ephemeral=True)
            return
        self.pagina -= 1
        self.atualizar_botoes()
        await interaction.response.edit_message(embed=self.embed_atual(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def btn_proximo(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message("❌ Só quem usou o comando pode navegar!", ephemeral=True)
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
        await ctx.send("❌ Nenhuma categoria encontrada!")
        return

    view = PaginadorApelidos(categorias, ctx.author.id)
    view.atualizar_botoes()
    await ctx.send(embed=view.embed_atual(), view=view)


@bot.command(name="silabas")
async def gerar_apelido(ctx):

    prefixos = ["telé", "pim", "wing", "ban", "splink", "hatio", "tchon", "tché", "ton", "xilé", "bu", "trel", "tchog", "chu", "bog", "cam", "top", "esn", "plog", "cop", "smô", "glo"]
    meios = ["bo", "ber", "slom", "de", "stron", "flin", "vis", "go", "lé", "gas", "le", "gle", "pis", "ban", "pó", "nó", "órn", "ólp", "glers"]
    sufixos = ["gas", "tos", "rox", "blus", "bum", "pito", "nlu", "lay", "pson", "clay", "klins", "fly", "flay", "vis", "mongo", "pisz", "bugas", "borg", "glets", "quets", "lers"]

    num_silabas = random.choice([2, 3])

    if num_silabas == 2:
        apelido = random.choice(prefixos) + random.choice(sufixos)
    else:
        apelido = random.choice(prefixos) + random.choice(meios) + random.choice(sufixos)

    apelido = apelido.capitalize()

    embed = discord.Embed(
        title="✨ Seu Apelido Aleatório",
        description=apelido,
        color=discord.Color.purple(),
    )
    await ctx.send(embed=embed)


SILABAS_SUPER = ["dri", "klo", "ver", "son", "as", "preu", "ble", "tor", "mas", "que", "mi", "guar", "dis", "in", "sla", "psa", "vo", "klan", "ni", "mõn", "gdon", "drio", "joer", "nog", "los", "sono", "õg", "nor", "dras", "le", "pur", "min", "xô", "mig", "ñor", "blis", "ter", "glov", "im", "dir", "ner", "xa", "mur", "lin", "plo", "pler", "nior"]


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
        await ctx.send("❌ Você precisa ser administrador do servidor para usar este comando!")
        return

    if not linhas_arquivo:
        await ctx.send("❌ Nenhum arquivo foi carregado!")
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
                        title="🌙 Teste forçado de alteração de apelido às 19:00",
                        description=f"🎉 O apelido do dia é: **{apelido_do_dia}**",
                        color=discord.Color.dark_blue(),
                    )
                    await canal.send(embed=embed)

                    if usuario_apelido is not None:
                        servidor = bot.get_guild(ctx.guild.id)
                        if servidor is not None:
                            membro = servidor.get_member(usuario_apelido)
                            if membro is not None:
                                nome_anterior = membro.nick if membro.nick else membro.name
                                await membro.edit(nick=apelido_do_dia)
                                await canal.send(
                                    f"✅ O apelido de **{nome_anterior}** foi alterado para: **{apelido_do_dia}**. {membro.mention}"
                                )

    except Exception as e:
        await ctx.send(f"❌ Erro ao testar: {e}")


@bot.command(name="testenovo")
@commands.has_permissions(administrator=True)
async def teste_novos_apelidos(ctx):
    """Força a verificação de novos apelidos agora e anuncia no canal (Admin)."""

    await ctx.send("🔄 Verificando novos apelidos no Google Docs...")

    try:
        if GOOGLE_DOCS_FILE_ID == "SEU_ID_AQUI":
            await ctx.send("❌ Google Docs ID não configurado!")
            return

        conteudo_principal = await asyncio.get_event_loop().run_in_executor(
            None, baixar_google_docs, GOOGLE_DOCS_FILE_ID
        )

        linhas_principal = [
            l.rstrip("\n").rstrip("\r") for l in conteudo_principal.split("\n") if l.strip()
        ]

        apelidos_principal = parse_categorias(linhas_principal)
        apelidos_comparativo = await carregar_comparativo()

        adicionados = {
            apelido: categoria
            for apelido, categoria in apelidos_principal.items()
            if apelido not in apelidos_comparativo
        }

        if adicionados:
            por_categoria: dict = {}
            for apelido, categoria in adicionados.items():
                por_categoria.setdefault(categoria, []).append(apelido)

            embed = discord.Embed(
                title=f"🆕 {len(adicionados)} Novo(s) Apelido(s) Detectado(s)!",
                color=discord.Color.teal(),
            )
            for categoria, apelidos_lista in por_categoria.items():
                embed.add_field(
                    name=f"📂 {categoria}",
                    value="\n".join(f"• **{a}**" for a in apelidos_lista),
                    inline=False,
                )
            embed.set_footer(text="Verificação manual via !testenovo")
            await ctx.send(embed=embed)

            await salvar_comparativo(apelidos_principal)

            configs = carregar_configs()
            for config in configs.values():
                canal_id = config.get("canal_meia_noite")
                if canal_id is None:
                    continue
                canal = bot.get_channel(canal_id)
                if canal is None:
                    continue

                embed_canal = discord.Embed(
                    title=f"🆕 {len(adicionados)} Novo(s) Apelido(s) Adicionado(s)!",
                    color=discord.Color.teal(),
                )
                for categoria, apelidos_lista in por_categoria.items():
                    embed_canal.add_field(
                        name=f"📂 {categoria}",
                        value="\n".join(f"• **{a}**" for a in apelidos_lista),
                        inline=False,
                    )
                embed_canal.set_footer(text="Adicionado via Google Docs")
                await canal.send(embed=embed_canal)

        else:
            await ctx.send("✅ Verificação concluída! Nenhum apelido novo.")

    except Exception as e:
        await ctx.send(f"❌ Erro na verificação: {e}")


@bot.command(name="ajuda", aliases=["comandos", "help"])
async def ajuda(ctx, *args):
    embed = discord.Embed(
        title="📖 Comandos do Bot",
        description="Lista de comandos disponíveis:",
        color=discord.Color.blue(),
    )
    embed.add_field(name="!recarregar", value="Recarrega o arquivo do Google Docs (Admin)", inline=False)
    embed.add_field(name="!apelido", value="Envia um apelido aleatório do arquivo carregado com categoria", inline=False)
    embed.add_field(name="!todos", value="Mostra todos os apelidos do arquivo separados por categoria", inline=False)
    embed.add_field(name="!quais / !categorias", value="Mostra todas as categorias com a quantidade de apelidos", inline=False)
    embed.add_field(name="!silabas", value="Gera um apelido aleatório com sílabas", inline=False)
    embed.add_field(name="!super-silaba", value="Gera um super apelido com duas palavras malucas", inline=False)
    embed.add_field(name="!teste", value="Testa a função de meia-noite (Admin)", inline=False)
    embed.add_field(name="!testenovo", value="Força verificação de novos apelidos no Docs agora (Admin)", inline=False)
    embed.add_field(name="!ajuda / !comandos / !help", value="Mostra esta mensagem", inline=False)
    await ctx.send(embed=embed)


# Token do Discord
token = os.getenv("DISCORD_TOKEN")

if not token:
    print("❌ DISCORD_TOKEN não configurado!")
    exit()

bot.run(token)
