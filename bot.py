#!/usr/bin/env python3
"""Unfold MoonVeil 2.x CFF string and numeric constants using Lua 5.1."""
from __future__ import annotations
import argparse, re, shutil, subprocess, sys, tempfile, os, stat, urllib.request
from pathlib import Path

OPS = ("qa", "ra", "sa", "ta", "ua")
LUA_HARNESS = r''' 
local MOD = 4294967296
local function u32(x) x=tonumber(x) or 0; x=x%MOD; if x<0 then x=x+MOD end; return x end
local function bitop(a,b,mode,...)
  a,b=u32(a),u32(b); local r,bitv=0,1
  for _=1,32 do
    local aa,bb=a%2,b%2
    if (mode=="and" and aa>=1 and bb>=1) or (mode=="or" and (aa>=1 or bb>=1)) or (mode=="xor" and ((aa>=1) ~= (bb>=1))) then r=r+bitv end
    a=math.floor(a/2); b=math.floor(b/2); bitv=bitv*2
    if mode~="xor" and a==0 and b==0 then break end
  end
  if select("#",...)>0 then return bitop(r,(...),mode,select(2,...)) end
  return r
end
local function band(a,b,...) if select("#",...)>0 then return band(bitop(a,b,"and"),...) end; return bitop(a,b,"and") end
local function bor(a,b,...) if select("#",...)>0 then return bor(bitop(a,b,"or"),...) end; return bitop(a,b,"or") end
local function bxor(a,b,...) if select("#",...)>0 then return bxor(bitop(a,b,"xor"),...) end; return bitop(a,b,"xor") end
local function bnot(a) return MOD-1-u32(a) end
local function lshift(a,n) n=u32(n)%32; a=u32(a); for _=1,n do a=(a*2)%MOD end; return a end
local function rshift(a,n) n=u32(n)%32; a=u32(a); for _=1,n do a=math.floor(a/2) end; return a end
local bit32={band=band,bor=bor,bxor=bxor,bnot=bnot,lshift=lshift,rshift=rshift,arshift=rshift}
local env={bit32=bit32,string=string,table=table,math=math,type=type,tostring=tostring,tonumber=tonumber,
 select=select,unpack=unpack,next=next,pairs=pairs,ipairs=ipairs,setmetatable=setmetatable,
 getmetatable=getmetatable,pcall=pcall,error=error,assert=assert,rawget=rawget,rawset=rawset,
 rawequal=rawequal,print=function() end}
env._G=env; env.getfenv=getfenv; env.setfenv=setfenv
local chunk,err=loadfile(arg[1]); if not chunk then io.stderr:write("load failed: ",tostring(err),"\n"); os.exit(1) end
setfenv(chunk,env)
local ok,rt=xpcall(chunk,debug.traceback); if not ok then io.stderr:write("bootstrap failed: ",tostring(rt),"\n"); os.exit(1) end
if type(rt)~="table" or type(rt.qa)~="function" then io.stderr:write("bootstrap did not return decoder table\n"); os.exit(1) end
local function emit(i,k,d) io.write(i,"\t",k,"\t",d,"\n"); io.flush() end
local function hex(s) return (s:gsub(".",function(c) return string.format("%02x",string.byte(c)) end)) end
for line in io.lines(arg[2]) do
 local idx,expr=line:match("^(%d+)\t(.*)$")
 if idx then
  local loader,lerr=loadstring("return function(rt) return "..expr.." end")
  if not loader then emit(idx,"error","compile") else
   local fn=loader(); local ok2,res=pcall(fn,rt)
   if not ok2 then emit(idx,"error","run")
   elseif type(res)=="string" then emit(idx,"string",hex(res))
   elseif type(res)=="number" then
    if res~=res then emit(idx,"number","0/0") elseif res==math.huge then emit(idx,"number","math.huge")
    elseif res==-math.huge then emit(idx,"number","-math.huge")
    elseif res==math.floor(res) and math.abs(res)<1e15 then emit(idx,"number",string.format("%.0f",res))
    else emit(idx,"number",string.format("%.17g",res)) end
   elseif type(res)=="boolean" then emit(idx,"boolean",tostring(res))
   else emit(idx,"error","type:"..type(res)) end
  end
 end
end
'''.lstrip()

def lua_version(lua: str) -> str:
    try:
        return subprocess.check_output([lua, "-v"], stderr=subprocess.STDOUT, text=True).strip()
    except (subprocess.CalledProcessError, OSError) as exc:
        return getattr(exc, "output", "") or str(exc)

def find_lua(explicit: str | None) -> str:
    if explicit:
        if not Path(explicit).exists() and shutil.which(explicit) is None:
            sys.exit(f"Lua executable not found: {explicit}")
        path = shutil.which(explicit) or explicit
        ver = lua_version(path)
        if not ver.startswith("Lua 5.1"):
            sys.exit(f"{ver}: Lua 5.1 is required")
        return path

    # หา lua51 หรือ lua_bin ในโฟลเดอร์ปัจจุบัน
    for bin_name in ("lua51", "lua_bin"):
        bin_path = Path(bin_name)
        if bin_path.exists():
            # พยายามให้สิทธิ์ exec เผื่อไว้
            try:
                bin_path.chmod(bin_path.stat().st_mode | stat.S_IEXEC)
            except Exception:
                pass
            if lua_version(str(bin_path)).startswith("Lua 5.1"):
                return str(bin_path.resolve())

    for name in ("lua5.1", "lua51", "lua"):
        path = shutil.which(name)
        if path and lua_version(path).startswith("Lua 5.1"):
            return path

    sys.exit("Lua 5.1 not found. Please upload a static lua5.1 binary named 'lua51' to the bot folder.")

def convert_binary_literals(src: str) -> str:
    out=[]; i=0; n=len(src); quote=None; esc=False; long_eq=None
    while i<n:
        c=src[i]
        if quote:
            out.append(c)
            if esc: esc=False
            elif c=="\\": esc=True
            elif c==quote: quote=None
            i+=1; continue
        if long_eq is not None:
            close="]"+"="*long_eq+"]"
            if src.startswith(close,i): out.append(close); i+=len(close); long_eq=None
            else: out.append(c); i+=1
            continue
        if src.startswith("--",i):
            if i+2<n and src[i+2]=="[":
                j=i+3; eq=0
                while j<n and src[j]=="=": eq+=1; j+=1
                if j<n and src[j]=="[":
                    close="]"+"="*eq+"]"; k=src.find(close,j+1); end=n if k<0 else k+len(close)
                    out.append(src[i:end]); i=end; continue
            nl=src.find("\n",i); end=n if nl<0 else nl+1; out.append(src[i:end]); i=end; continue
        if c in "\"'": quote=c; out.append(c); i+=1; continue
        if c=="[":
            j=i+1; eq=0
            while j<n and src[j]=="=": eq+=1; j+=1
            if j<n and src[j]=="[": long_eq=eq; out.append(src[i:j+1]); i=j+1; continue
        if c=="0" and i+1<n and src[i+1] in "bB":
            j=i+2
            while j<n and src[j] in "01": j+=1
            if j>i+2: out.append(str(int(src[i+2:j],2))); i=j; continue
        out.append(c); i+=1
    return "".join(out)

def neuter_entry(src: str) -> str:
    pat=re.compile(r"return\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*k\s*\(\s*\)\s*\(\s*\.\.\.\s*\)")
    new,n=pat.subn(r"return \1 ",src)
    if n!=1:
        sys.exit(f"expected exactly one MoonVeil entry trampoline; found {n}. Refusing to run the payload.")
    return new

def _scan_call(src: str, colon: int):
    k=colon-1
    if k<0 or not (src[k].isalnum() or src[k]=="_"): return None
    start=k
    while start>0 and (src[start-1].isalnum() or src[start-1]=="_"): start-=1
    op_end=colon+1
    while op_end<len(src) and (src[op_end].isalnum() or src[op_end]=="_"): op_end+=1
    op=src[colon+1:op_end]
    if op not in OPS or op_end>=len(src) or src[op_end]!="(": return None
    p=op_end+1; depth=1; quote=None; esc=False
    while p<len(src):
        c=src[p]
        if quote:
            if esc: esc=False
            elif c=="\\": esc=True
            elif c==quote: quote=None
        else:
            if c in "\"'": quote=c
            elif c=="(": depth+=1
            elif c==")":
                depth-=1
                if depth==0: return start,p+1,src[start:colon],op,src[op_end+1:p]
        p+=1
    return None

def expr_is_safe(recv: str, op: str, args: str) -> str | None:
    expr_args = re.sub(rf"\b{re.escape(recv)}\b", "__rt", args)
    expr = f"__rt:{op}({expr_args})"
    tmp = expr
    tmp = re.sub(r"__rt:(?:qa|ra|sa|ta|ua)", "", tmp)
    tmp = re.sub(r"__rt\.ba", "", tmp)
    tmp = re.sub(r'"(?:\\.|[^"\\])*"', "", tmp)
    tmp = re.sub(r"'(?:\\.|[^'\\])*'", "", tmp)
    tmp = re.sub(r"-?0[xX][0-9a-fA-F]+", "", tmp)
    tmp = re.sub(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", "", tmp)
    tmp = re.sub(r"[\s,().]", "", tmp)
    return expr if not tmp else None

def find_sites(src: str) -> list[dict]:
    sites=[]; i=0
    while True:
        colon=src.find(":",i)
        if colon<0: break
        found=_scan_call(src,colon)
        if not found: i=colon+1; continue
        start,end,recv,op,args=found; expr=expr_is_safe(recv,op,args); repl_start=start
        window=src[max(0,start-160):start]
        m=re.search(rf"{re.escape(recv)}\.pa\[[^\]]+\]or\s*$",window)
        if m and window.endswith(m.group(0)): repl_start=start-len(m.group(0))
        if expr is not None: sites.append({"start":repl_start,"end":end,"expr":expr,"op":op})
        i=end
    sites.sort(key=lambda x:x["start"]); kept=[]; last=-1
    for s in sites:
        if s["start"]>=last: kept.append(s); last=s["end"]
    return kept

def lua_quote(raw: bytes) -> str:
    out=['"']
    for b in raw:
        if b==34: out.append('\\"')
        elif b==92: out.append('\\\\')
        elif b==10: out.append('\\n')
        elif b==13: out.append('\\r')
        elif b==9: out.append('\\t')
        elif 32<=b<127: out.append(chr(b))
        else: out.append(f"\\{b:03d}")
    return "".join(out)+'"'

def evaluate(lua: str, patched: str, sites: list[dict]) -> list[str | None]:
    with tempfile.TemporaryDirectory(prefix="mvdeob-") as td:
        root=Path(td); (root/"patched.lua").write_text(patched,encoding="utf-8")
        (root/"harness.lua").write_text(LUA_HARNESS,encoding="utf-8"); jobs=root/"jobs.txt"
        with jobs.open("w",encoding="utf-8") as f:
            for i,s in enumerate(sites): f.write(f"{i}\t{s['expr']}\n")
        try: proc=subprocess.run([lua,str(root/"harness.lua"),str(root/"patched.lua"),str(jobs)],capture_output=True,text=True,timeout=180)
        except subprocess.TimeoutExpired: sys.exit("decoder timed out after 180 seconds")
        if proc.returncode: sys.exit("decoder bootstrap failed: "+(proc.stderr.strip() or f"exit {proc.returncode}"))
        vals=[None]*len(sites)
        for line in proc.stdout.splitlines():
            parts=line.split("\t",2)
            if len(parts)!=3: continue
            idx_s,kind,data=parts
            try: idx=int(idx_s)
            except ValueError: continue
            if not 0<=idx<len(vals): continue
            if kind=="string":
                try: vals[idx]=lua_quote(bytes.fromhex(data))
                except ValueError: vals[idx]=None
            elif kind in ("number","boolean"): vals[idx]=data
        return vals

def apply(src: str, sites: list[dict], values: list[str | None]):
    out=src; done=0
    for site,value in zip(reversed(sites),reversed(values)):
        if value is None: continue
        out=out[:site["start"]]+value+" "+out[site["end"]:]; done+=1
    return out,done

def rough_breaks(src: str) -> str:
    out=[]; i=0; n=len(src); quote=None; esc=False
    kws=("function","then","do","else","elseif","end","repeat","until")
    while i<n:
        c=src[i]
        if quote:
            out.append(c)
            if esc: esc=False
            elif c=="\\": esc=True
            elif c==quote: quote=None
            i+=1; continue
        if src.startswith("--",i):
            nl=src.find("\n",i); end=n if nl<0 else nl+1; out.append(src[i:end]); i=end; continue
        if c in "\"'": quote=c; out.append(c); i+=1; continue
        matched=False
        for kw in kws:
            if src.startswith(kw,i):
                before=src[i-1] if i else " "; after=src[i+len(kw)] if i+len(kw)<n else " "
                if before.isalnum() or before=="_" or after.isalnum() or after=="_": continue
                if kw in ("function","elseif","else","repeat","until") and out and out[-1]!="\n": out.append("\n")
                out.append(kw); i+=len(kw)
                if kw in ("then","do","else","end","repeat","until"): out.append("\n")
                matched=True; break
        if not matched: out.append(c); i+=1
    return "".join(out)

def main() -> None:
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument("input",type=Path); ap.add_argument("-o","--output",type=Path)
    ap.add_argument("--lua",help="path to Lua 5.1 interpreter"); ap.add_argument("--no-pretty",action="store_true"); args=ap.parse_args()
    lua=find_lua(args.lua); ver=lua_version(lua)
    try: original=args.input.read_text(encoding="utf-8",errors="replace")
    except OSError as e: sys.exit(f"cannot read input: {e}")
    if "MoonVeil" not in original and ":qa(" not in original and '.ba"' not in original:
        sys.exit("input does not look like a MoonVeil CFF bundle")
    pre=convert_binary_literals(original); patched=neuter_entry(pre); sites=find_sites(pre)
    if not sites: sys.exit("no safe qa/ra/sa/ta/ua call sites found")
    values=evaluate(lua,patched,sites); unfolded,done=apply(pre,sites,values); failed=len(sites)-done
    header=("-- MoonVeil CFF constants unfolded\n-- strings/numbers only; control flow is still a state machine\n"
            f"-- folded {done} call sites, left {failed} untouched\n")
    if not args.no_pretty: unfolded=rough_breaks(unfolded)
    result=header+unfolded
    if not result.endswith("\n"): result+="\n"
    dest=args.output or args.input.with_suffix(args.input.suffix+".deob.lua")
    try: dest.write_text(result,encoding="utf-8")
    except OSError as e: sys.exit(f"cannot write output: {e}")
    print(f"lua: {ver}\nsites: {len(sites)}  folded: {done}  failed: {failed}\nwrote {dest}")

import asyncio
import io
import ipaddress
import os
import signal
import socket
from urllib.parse import urlsplit

MAX_INPUT_BYTES = int(os.getenv("MAX_INPUT_BYTES", str(2 * 1024 * 1024)))
MAX_OUTPUT_BYTES = int(os.getenv("MAX_OUTPUT_BYTES", str(8 * 1024 * 1024)))
DOWNLOAD_TIMEOUT = float(os.getenv("DOWNLOAD_TIMEOUT", "25"))
BOT_WORKERS = max(1, int(os.getenv("BOT_WORKERS", "2")))
USER_COOLDOWN = float(os.getenv("USER_COOLDOWN", "10"))
PROCESS_TIMEOUT = float(os.getenv("PROCESS_TIMEOUT", "210"))

def _is_public_ip(ip):
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast:
        return False
    if ip.is_reserved or ip.is_unspecified:
        return False
    return ip.is_global

class PublicOnlyResolver:
    async def resolve(self, host, port=0, family=socket.AF_UNSPEC):
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, port, family=family,
                                       type=socket.SOCK_STREAM)
        results, seen = [], set()
        for fam, _, proto, _, sockaddr in infos:
            ip_str = sockaddr[0]
            try:
                parsed = ipaddress.ip_address(ip_str)
            except ValueError:
                raise OSError("URL host resolved to an unparsable IP")
            if not _is_public_ip(parsed):
                raise OSError("URL host resolves to a non-public IP address")
            key = (fam, ip_str)
            if key in seen:
                continue
            seen.add(key)
            results.append({"hostname": host, "host": ip_str, "port": port,
                            "family": fam, "proto": proto,
                            "flags": socket.AI_NUMERICHOST})
        if not results:
            raise OSError("URL host did not resolve to a public IP address")
        return results

    async def close(self):
        return None

def validate_url(url: str) -> str:
    if not url or len(url) > 2048:
        raise ValueError("URL too long")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid URL") from exc
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Only http:// or https:// URLs are supported")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL must not contain username/password")
    if port is not None and not (1 <= port <= 65535):
        raise ValueError("Invalid port number")
    try:
        ip = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return url
    if not _is_public_ip(ip):
        raise ValueError("URL pointing to a private or special IP is not allowed")
    return url

async def fetch_lua(url: str) -> bytes:
    import aiohttp
    url = validate_url(url)
    timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT, connect=8, sock_read=10)
    connector = aiohttp.TCPConnector(
        resolver=PublicOnlyResolver(), use_dns_cache=False, limit=4,
    )
    async with aiohttp.ClientSession(
        timeout=timeout, connector=connector,
        headers={"User-Agent": "MoonVeil-Deob-Bot/1.0"},
        trust_env=False,
    ) as session:
        async with session.get(url, allow_redirects=False) as response:
            if 300 <= response.status < 400:
                raise ValueError("URL redirects - please use the final URL directly")
            if response.status != 200:
                raise ValueError(f"Download failed: HTTP {response.status}")
            length = response.headers.get("Content-Length")
            if length:
                try:
                    if int(length) > MAX_INPUT_BYTES:
                        raise ValueError(
                            f"File too large (max {MAX_INPUT_BYTES} bytes)")
                except ValueError as exc:
                    if "File too large" in str(exc):
                        raise
            chunks, total = [], 0
            async for chunk in response.content.iter_chunked(65536):
                total += len(chunk)
                if total > MAX_INPUT_BYTES:
                    raise ValueError(
                        f"File too large (max {MAX_INPUT_BYTES} bytes)")
                chunks.append(chunk)
            data = b"".join(chunks)
            if not data:
                raise ValueError("Empty file")
            return data

async def run_deobfuscator(source: bytes):
    with tempfile.TemporaryDirectory(prefix="moonveil-bot-") as td:
        root = Path(td)
        src = root / "input.lua"
        dst = root / "output.lua"
        src.write_bytes(source)
        script = Path(__file__).resolve()
        
        # หา path ของ Lua ให้แน่นอน (ใช้ absolute path)
        lua_override = os.getenv("LUA51_PATH", "").strip()
        if lua_override:
            lua_bin = lua_override
        else:
            try:
                lua_bin = find_lua(None)
            except SystemExit as e:
                raise ValueError(str(e))
                
        command = [lua_bin, str(script), str(src), "-o", str(dst), "--no-pretty"]
        
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=td,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=PROCESS_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise ValueError(f"Processing timed out (over {PROCESS_TIMEOUT:.0f}s)")
        if proc.returncode != 0:
            message = (stderr or stdout).decode("utf-8", "replace").strip()
            last = message.splitlines()[-1] if message else "Processing failed"
            raise ValueError(last[:300])
        if not dst.is_file():
            raise ValueError("Output file not found")
        result = dst.read_bytes()
        if len(result) > MAX_OUTPUT_BYTES:
            raise ValueError(
                f"Result too large for attachment ({MAX_OUTPUT_BYTES} bytes)")
        return result, stdout.decode("utf-8", "replace").strip()

async def start_discord_bot():
    try:
        import discord
        from discord import app_commands
    except ImportError:
        raise SystemExit("Missing dependency: install discord.py and aiohttp")

    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise SystemExit("Set the DISCORD_TOKEN environment variable first")

    lua = find_lua(os.getenv("LUA51_PATH") or None)
    print(f"[bot] {lua_version(lua)}; input limit={MAX_INPUT_BYTES} bytes; "
          f"workers={BOT_WORKERS}")

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)
    semaphore = asyncio.Semaphore(BOT_WORKERS)
    last_seen = {}
    stop_event = asyncio.Event()

    def _cooldown_ok(user_id):
        now = asyncio.get_running_loop().time()
        last = last_seen.get(user_id, 0.0)
        remaining = USER_COOLDOWN - (now - last)
        if remaining > 0:
            return remaining
        last_seen[user_id] = now
        return 0.0

    @tree.command(
        name="moonveil",
        description="Unfold MoonVeil CFF constants from a public Lua file URL",
    )
    @app_commands.describe(url="Direct link to a publicly accessible .lua file")
    async def moonveil(interaction: discord.Interaction, url: str):
        wait = _cooldown_ok(interaction.user.id)
        if wait > 0:
            await interaction.response.send_message(
                f"Please wait {wait:.0f} seconds before using this command again",
                ephemeral=True)
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        async with semaphore:
            try:
                data = await fetch_lua(url)
                result, summary = await run_deobfuscator(data)
                file = discord.File(
                    fp=io.BytesIO(result), filename="moonveil_deobfuscated.lua")
                details = (f"Done - received {len(data):,} bytes - "
                           f"output {len(result):,} bytes")
                if summary:
                    keep = [ln for ln in summary.splitlines()
                            if ln.startswith(("lua:", "sites:"))]
                    if keep:
                        details += "\n" + "\n".join(keep)
                await interaction.followup.send(
                    content=details[:1900], file=file, ephemeral=True)
            except (ValueError, OSError, asyncio.TimeoutError) as exc:
                await interaction.followup.send(
                    f"Failed: {str(exc)[:500]}", ephemeral=True)
            except Exception as exc:
                print(f"[bot] unhandled: {type(exc).__name__}: {exc}")
                await interaction.followup.send(
                    "Internal error - check host logs", ephemeral=True)

    @client.event
    async def on_ready():
        try:
            synced = await tree.sync()
            print(f"[bot] logged in as {client.user}; synced {len(synced)} command(s)")
        except Exception as exc:
            print(f"[bot] command sync failed: {type(exc).__name__}: {exc}")

    def _install_signal_handlers(loop):
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except NotImplementedError:
                pass

    _install_signal_handlers(asyncio.get_running_loop())

    run_task = asyncio.create_task(client.start(token))
    stop_task = asyncio.create_task(stop_event.wait())
    done, pending = await asyncio.wait(
        {run_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)

    for task in pending:
        task.cancel()
    for task in pending:
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    if not client.is_closed():
        try:
            await client.close()
        except Exception:
            pass
    if run_task.done() and run_task.exception() is not None:
        raise run_task.exception()
    print("[bot] shutdown complete")

def dispatch() -> None:
    bot_mode = "--bot" in sys.argv or bool(os.getenv("DISCORD_TOKEN"))
    
    if bot_mode:
        if "--bot" in sys.argv:
            sys.argv.remove("--bot")
        asyncio.run(start_discord_bot())
    else:
        main()

if __name__ == "__main__":
    dispatch()