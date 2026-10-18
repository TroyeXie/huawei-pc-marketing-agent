"""Zhi Hua: a source-grounded Huawei PC marketing agent.

The public server exposes generated work only. Credentials, source documents and
raw knowledge-base indexes remain outside the source tree and public routes.
"""
from __future__ import annotations
import os, json, time, uuid, threading, hashlib, mimetypes, logging
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict, deque
from typing import Any
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE = Path(__file__).resolve().parent
RUNTIME = Path(os.getenv('ZHIHUA_RUNTIME_DIR', '/tmp/zhi-hua-runtime')).resolve()
RUNTIME.mkdir(parents=True, exist_ok=True)
GITHUB_URL = os.getenv('GITHUB_URL', 'https://github.com/TroyeXie/huawei-pc-marketing-agent')
MODEL_URL = os.getenv('MODEL_API_URL', '')
PRODUCTS = [
 {'id':'matebook14-ultra','name':'MateBook 14 酷睿 Ultra'},
 {'id':'matebook14-harmony','name':'HUAWEI MateBook 14 鸿蒙版'},
 {'id':'matebook-pro','name':'HUAWEI MateBook Pro'},
 {'id':'matebook-pro-s','name':'HUAWEI MateBook Pro S'},
 {'id':'matebook-fold','name':'HUAWEI MateBook Fold 非凡大师'},
 {'id':'matebook-d14','name':'HUAWEI MateBook D 14'},
 {'id':'matebook-d16','name':'HUAWEI MateBook D 16'},
 {'id':'matebook-d16-se','name':'HUAWEI MateBook D 16 SE'},
 {'id':'matebook-gt','name':'HUAWEI MateBook GT'},
 {'id':'matebook-x','name':'HUAWEI MateBook X 系列'},
]
SCENES=['开学季','职场办公','内容创作','家庭换新','企业采购','门店到访','新品首发','节日营销']
PLATFORMS=['小红书','公众号','门店朋友圈','门店海报']
app=FastAPI(title='知华·有据',docs_url=None,redoc_url=None,openapi_url=None)
app.mount('/static',StaticFiles(directory=BASE/'static'),name='static')
_lock=threading.RLock()
_run_lock=threading.BoundedSemaphore(int(os.getenv('MAX_CONCURRENT_RUNS','2')))
_client=None
_runs:dict[str,dict]={}
_media:dict[tuple[str,str],Path]={}
_limits=defaultdict(deque)
_status_cache={'at':0.,'data':{}}
_examples_cache=None

class GenerateRequest(BaseModel):
 product:str=Field(default='auto',max_length=120)
 audience:str=Field(default='',max_length=200)
 scene:str=Field(default='',max_length=100)
 platform:str=Field(default='',max_length=80)
 goal:str=Field(default='',max_length=500)
 brief:str=Field(default='',max_length=3000)
class CheckRequest(BaseModel):
 text:str=Field(max_length=15000)
 product:str=Field(default='',max_length=120)
 run_id:str=Field(default='',max_length=64)

@app.middleware('http')
async def headers(request:Request,call_next):
 try: content_length=int(request.headers.get('content-length','0') or 0)
 except ValueError: return JSONResponse({'status':'error','message':'请求长度格式不正确。'},status_code=400)
 if content_length>100_000:
  return JSONResponse({'status':'error','message':'内容过长，请缩短任务描述。'},status_code=413)
 response=await call_next(request)
 response.headers['X-Content-Type-Options']='nosniff'
 response.headers['Referrer-Policy']='same-origin'
 if request.url.path.startswith('/api/'):
  response.headers['Cache-Control']='no-store'
 return response

def client():
 global _client
 with _lock:
  if _client is None:
   from ima_client import IMAClient
   _client=IMAClient()
  return _client

def current_time():
 return datetime.now(timezone.utc).isoformat(timespec='seconds')

def scrub(value:Any):
 """Do not return source caches, credentials or private file addresses."""
 if isinstance(value,dict):
  return {k:scrub(v) for k,v in value.items() if k not in {'local_path','kb_id','knowledge_base_id','media_id','headers','api_key','client_id','raw','raw_response','signed_url'}}
 if isinstance(value,list): return [scrub(x) for x in value]
 if isinstance(value,str):
  for key in ['IMA_OPENAPI_CLIENTID','IMA_OPENAPI_APIKEY','MODEL_API_KEY']:
   secret=os.getenv(key,'')
   if secret: value=value.replace(secret,'[已隐藏]')
  if value.startswith(str(RUNTIME)): return '[内部素材]'
  return value
 return value

def resolve_product(v,brief=''):
 for p in PRODUCTS:
  if v in (p['id'],p['name']):return p['name']
 if v in ('','auto'):
  matches=[p['name'] for p in PRODUCTS if p['name'].replace('HUAWEI ','') in brief]
  if len(matches)==1:return matches[0]
 return v

@app.get('/')
def home():return FileResponse(BASE/'static/index.html',headers={'Cache-Control':'no-cache'})
@app.get('/healthz')
def health():return {'status':'ok','application':'zhi-hua-agent'}
@app.get('/api/catalog')
def catalog():
 from public_evidence import supported_products
 available=set(supported_products())
 return {'products':[dict(p,available=p['name'] in available,availability_note='' if p['name'] in available else '来源待核对') for p in PRODUCTS],'platforms':PLATFORMS,'scenes':SCENES}
@app.get('/api/status')
def status():
 now=time.monotonic()
 if now-_status_cache['at']<30:return _status_cache['data']
 output={'ima_connected':False,'knowledge_base':'任德耀新建华为PC知识库','account_label':'参赛团队 ima 账号','llm_configured':bool(os.getenv('MODEL_API_KEY') and os.getenv('MODEL_API_URL') and os.getenv('MODEL_NAME')),'mode':'live','github_url':GITHUB_URL,'message':''}
 try:
  data=client().discover()
  output.update({'ima_connected':data.get('status') in ('connected','ok','success',True),'knowledge_base':data.get('kb_name',output['knowledge_base']),'account_label':data.get('account_label',output['account_label']),'message':data.get('message','共享知识库连接成功。')})
 except Exception as e:
  output['message']='ima 连接暂不可用：'+str(e)
 if not output['llm_configured']:output['message']+=' 模型服务尚未配置。'
 output=scrub(output)
 _status_cache.update(at=now,data=output)
 return output

def preflight(payload):
 questions=[]
 if payload['product'] in ('','auto'):questions.append('请指定产品完整型号；例如 MateBook 14 酷睿 Ultra，或 MateBook 14 鸿蒙版。')
 elif payload['product'].replace('HUAWEI','').strip().lower() in ('matebook 14','matebook14'):
  questions.append('MateBook 14 有不同平台版本。请选择酷睿 Ultra 版或鸿蒙版，以免参数和图片混用。')
 elif payload['product'] not in {p['name'] for p in PRODUCTS}:
  questions.append('请从产品列表选择一个完整型号；比较多个型号时，请先分别核对后再组织对比。')
 else:
  from public_evidence import supported_products
  if payload['product'] not in supported_products():questions.append('该型号的来源核对尚未完成。请先选择已核对型号，再指定人群、场景和平台。')
 if not payload['audience'].strip():questions.append('内容面向哪类人群？例如大学新生、差旅办公者或附近到店顾客。')
 if not payload['scene'].strip():questions.append('本次使用场景是什么？例如开学季、职场办公或门店到访。')
 if payload['platform'] not in PLATFORMS:questions.append('请选择小红书、公众号、门店朋友圈或门店海报。')
 return questions

def normalize_materials(materials,run_id):
 """Register only selected product assets behind opaque per-run paths."""
 out=dict(materials)
 selected=[]
 for i,img in enumerate(materials.get('images',[])[:12]):
  im=dict(img)
  path=im.get('local_path')
  if path and Path(path).is_file():
   full=Path(path).resolve()
   if full.is_relative_to(RUNTIME):
    ident=hashlib.sha256((run_id+str(i)+str(full)).encode()).hexdigest()[:18]
    _media[(run_id,ident)]=full
    im.update(id=ident,url=f'/api/media/{run_id}/{ident}',status=im.get('status','verified'),model=materials.get('product_name',''))
  source=im.get('source',{})
  if isinstance(source,dict):
   im.setdefault('source_title',source.get('title',im.get('title','ima 图片')))
   im['source_url']='https://ima.qq.com/wikis'
  im['source_url']='https://ima.qq.com/wikis'
  selected.append(im)
 out['images']=selected
 for doc in out.get('documents',[]):
  if isinstance(doc.get('source'),dict):doc['source']['url']='https://ima.qq.com/wikis'
 return out

@app.post('/api/generate')
def generate(body:GenerateRequest,request:Request):
 payload=body.model_dump()
 payload['product']=resolve_product(payload['product'],payload['brief'])
 questions=preflight(payload)
 if questions:return {'status':'needs_clarification','questions':questions,'message':'补充这些信息后即可生成。','mode':'live'}
 host=request.client.host if request.client else 'unknown'
 now=time.monotonic()
 with _lock:
  q=_limits[host]
  while q and now-q[0]>3600:q.popleft()
  if len(q)>=int(os.getenv('MAX_RUNS_PER_HOUR','24')):
   return JSONResponse({'status':'error','message':'本小时生成次数较多，请稍后再试。'},status_code=429)
  q.append(now)
 if not _run_lock.acquire(blocking=False):return JSONResponse({'status':'error','message':'当前任务正在运行，请稍后再试。'},status_code=429)
 run_id=uuid.uuid4().hex
 started=time.monotonic()
 trace=[]
 try:
  from engine import generate as run_engine
  materials=client().collect_product(payload['product'])
  trace.append({'step':'ima 资料与图片检索','status':'completed','detail':f"指定共享库；产品：{payload['product']}；取得 {len(materials.get('documents',[]))} 份资料、{len(materials.get('images',[]))} 个图片候选。",'ms':int((time.monotonic()-started)*1000)})
  materials=normalize_materials(materials,run_id)
  result=run_engine(payload,materials)
  result['trace']=trace+result.get('trace',[])
  result.update(run_id=run_id,generated_at=current_time(),elapsed_ms=int((time.monotonic()-started)*1000),input=payload)
  result['material_provenance']={'provider':'ima OpenAPI','knowledge_base':materials.get('kb_name','任德耀新建华为PC知识库'),'fresh_ima_retrieval_for_this_run':True,'note':'本次任务通过 ima 接口检索产品事实与图片，再执行生成和核验。'}
  result=scrub(result)
  with _lock:_runs[run_id]=result
  (RUNTIME/'runs').mkdir(exist_ok=True)
  (RUNTIME/'runs'/f'{run_id}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
  return result
 except Exception as e:
  logging.getLogger('zhi-hua').warning('Generation failed: %s',type(e).__name__)
  return {'status':'error','message':scrub(str(e)),'trace':trace,'run_id':run_id,'mode':'live','elapsed_ms':int((time.monotonic()-started)*1000)}
 finally:_run_lock.release()

@app.get('/api/media/{run_id}/{image_id}')
def image_file(run_id:str,image_id:str):
 path=_media.get((run_id,image_id))
 if path is None or not path.is_file():raise HTTPException(404,'素材已过期，请重新生成。')
 media_type=mimetypes.guess_type(path.name)[0] or 'image/jpeg'
 if not media_type.startswith('image/'):raise HTTPException(404)
 return FileResponse(path,media_type=media_type,headers={'Cache-Control':'private, max-age=1800'})

@app.get('/api/examples')
def examples():
 global _examples_cache
 if _examples_cache is not None:return _examples_cache
 path=BASE/'examples'/'index.json'
 if not path.exists():return {'examples':[]}
 data=json.loads(path.read_text(encoding='utf-8'))
 for entry in data.get('examples',[]):
  result=entry.get('result',{})
  run_id=result.get('run_id')
  if run_id:
   with _lock:_runs.setdefault(run_id,result)
 _examples_cache=scrub(data)
 return _examples_cache

@app.post('/api/check')
def check(body:CheckRequest):
 from engine import check_text
 if body.run_id and body.run_id not in _runs:examples()
 facts=_runs.get(body.run_id,{}).get('facts',[]) if body.run_id else []
 checks=check_text(body.text,facts)
 blocked=any(c.get('status') in ('fail','blocked','rejected','error') for c in checks)
 return {'status':'blocked' if blocked else 'ok','checks':checks,'message':'已执行规则核验。'+('同时对照本次运行的已验证事实。' if facts else '本次未绑定已验证事实；产品事实仍需资料确认。')}

@app.get('/api/runs/{run_id}/export')
def export_run(run_id:str,format:str='md'):
 if format not in ('md','json'):raise HTTPException(400,'仅支持 md 或 json 格式。')
 if run_id not in _runs:examples()
 data=_runs.get(run_id)
 if data is None:raise HTTPException(404,'运行记录不存在。')
 if data.get('status') not in ('success','ok'):
  report={key:data[key] for key in ('status','message','run_id','generated_at','input','questions','facts','checks','trace') if key in data}
  content=json.dumps(report,ensure_ascii=False,indent=2) if format=='json' else '# 知华·有据 · 任务核验记录\n\n此记录不包含未通过核验的成稿正文。\n\n'+str(report.get('message',''))+'\n\n'+'\n'.join('- ['+str(c.get('status',''))+'] '+str(c.get('type',''))+'：'+str(c.get('claim',''))+'；'+str(c.get('action','')) for c in report.get('checks',[]))
 else:content=json.dumps(data,ensure_ascii=False,indent=2) if format=='json' else data.get('markdown','')
 return Response(content,media_type='application/json' if format=='json' else 'text/markdown; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="zhi-hua-{run_id[:8]}.{format}"'})

if __name__=='__main__':
 import uvicorn
 uvicorn.run(app,host='0.0.0.0',port=int(os.getenv('PORT','7860')),log_level='warning')
