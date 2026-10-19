"""Select controlled Huawei support excerpts by exact PC and workflow.

This source is official_huawei, separate from the ima adapter. Only quoted
source text is sent to the model; editorial summaries are display metadata.
"""
from __future__ import annotations
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

PATH=Path(__file__).with_name('data')/'ecosystem_evidence.json'
SOURCE_PATHS={
 'HW-ECO-KEYBOARD-OPERATION':'/cn/support/content/zh-cn16051201/',
 'HW-ECO-KEYBOARD-DEVICES':'/cn/support/content/zh-cn16050145/',
 'HW-ECO-CONTINUITY':'/cn/support/content/zh-cn16051202/',
 'HW-ECO-CONTINUITY-APPS':'/cn/support/content/zh-cn16050156/',
 'HW-ECO-TABLET-DISPLAY':'/cn/support/content/zh-cn16093701/',
 'HW-ECO-TABLET-DISPLAY-BOUNDARY':'/cn/support/content/zh-cn16049994/',
}
GROUPS={'接续':('HW-ECO-CONTINUITY','HW-ECO-CONTINUITY-APPS'),
        '键鼠共享':('HW-ECO-KEYBOARD-OPERATION','HW-ECO-KEYBOARD-DEVICES'),
        '平板副屏':('HW-ECO-TABLET-DISPLAY','HW-ECO-TABLET-DISPLAY-BOUNDARY')}
# The display explanation contains its own feature statement and version
# limits, including Fold. Its generic operation page is optional for Fold.
REQUIRED_SOURCES={**GROUPS,'平板副屏':('HW-ECO-TABLET-DISPLAY-BOUNDARY',)}


def catalog() -> dict:
    if not PATH.is_file():return {'sources':[],'recommended_examples':[]}
    raw=json.loads(PATH.read_text(encoding='utf-8'))
    if raw.get('schema_version')!=1 or raw.get('provider')!='official_huawei':
        raise ValueError('生态资料目录格式不匹配。')
    sources=[]
    for item in raw.get('sources',[]):
        url=urlsplit(str(item.get('url','')))
        if (item.get('source_id') not in SOURCE_PATHS or url.scheme!='https' or url.netloc!='consumer.huawei.com'
                or url.path!=SOURCE_PATHS[item['source_id']] or url.query or url.fragment):continue
        if not isinstance(item.get('verified_pc_models'),list):continue
        sources.append(item)
    return {**raw,'sources':sources}


def select_documents(product: str,payload: dict) -> tuple[list[dict],dict]:
    raw=catalog()
    scene=str(payload.get('scene',''))
    profile=payload.get('store_profile') if isinstance(payload.get('store_profile'),dict) else {}
    query=' '.join(str(payload.get(key,'') or '') for key in ('brief','content_angle','scene'))+' '+str(profile.get('customer_question',''))
    topics=[]
    if any(word in query for word in ('接续','备忘','手机记录','手机记下')):topics.append('接续')
    if any(word in query for word in ('键鼠共享','跨设备键鼠','拖拽文本')):topics.append('键鼠共享')
    if any(word in query for word in ('平板副屏','第二块屏','无线投屏','扩展屏幕','平板作为副屏')):topics.append('平板副屏')
    eligible=[item for item in raw['sources'] if product in item['verified_pc_models']
              and isinstance(item.get('source_excerpt'),str) and len(item['source_excerpt'].strip())>=40]
    available_ids={item['source_id'] for item in eligible}
    supported_topics=[topic for topic,required in REQUIRED_SOURCES.items() if set(required)<=available_ids]
    if scene=='生态体验' and not topics:topics=supported_topics or list(GROUPS)
    available_topics=[topic for topic in topics if topic in supported_topics]
    missing_topics=[topic for topic in topics if topic not in supported_topics]
    wanted={source_id for topic in available_topics for source_id in GROUPS[topic]}
    selected=[item for item in eligible if item['source_id'] in wanted]
    documents=[]
    for item in selected:
        excerpt=item.get('source_excerpt')
        # A short quote may establish a condition but cannot make an editorial
        # feature summary into an original document. The complete excerpt is
        # required for creative use of the workflow.
        if not isinstance(excerpt,str) or len(excerpt.strip())<40:continue
        documents.append({'title':item['title'],'text':excerpt.strip(),
                          'locator':item['source_id'],'source_url':item['url'],
                          'source':{'provider':'official_huawei','title':item['title'],'url':item['url'],
                                    'public_source_url':item['url'],'model_context':product,
                                    'public_verified':True,'public_verified_at':raw.get('verified_at',''),
                                    'retrieved_at':item.get('source_verified_at','')}})
    manifest={'provider':'official_huawei','verified_at':raw.get('verified_at',''),'topics':topics,
              'available_topics':available_topics,'missing_topics':missing_topics,'supported_topics':supported_topics,
              'source_ids':[doc['locator'] for doc in documents],
              'source_urls':[doc['source_url'] for doc in documents],
              'matched_document_count':len(documents),'requested':bool(topics),
              'message':('当前完整型号缺少'+ '、'.join(missing_topics)+'的完整功能及适用条件原文。') if missing_topics else
                        '已载入该完整型号适用的华为支持原文。' if documents else '当前完整型号没有匹配的生态功能原文。' if topics else ''}
    return documents,manifest
