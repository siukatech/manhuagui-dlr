from unittest import skip
import lzstring
import re
import os
import requests
import random
import json
import bs4
import time
from PIL import Image
from types import SimpleNamespace

HTTP_HEADER = {
    'accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
    'accept-encoding': 'gzip, deflate, br',
    'accept-language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
    'cache-control': 'no-cache',
    'pragma': 'no-cache',
    'referer': 'https://www.manhuagui.com/',
    'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"macOS"',
    'sec-fetch-dest': 'image',
    'sec-fetch-mode': 'no-cors',
    'sec-fetch-site': 'cross-site',
    'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

class manhuagui_comic:
    _CHANNEL_BASE = '.hamreus.com' # image bed base
    _CHANNELS = ['i', 'eu', 'us']
    _DOWNLOAD_FOLDER = 'Downloads'

    def __init__(self, bid : int, proxies : list=None, proxy_config : dict={'mode': 'none', 'verify': True}, convert : bool=True, convert_format : str=None, tunnel : int=0, skip_existed : bool=False, page_delay : float=0.5, subdomain : str='tw'):
        self._host = f"https://{subdomain}.manhuagui.com"
        self._comic_url_base = self._host + '/comic/'
        
        self._bid = bid # book id
        self._url = self._comic_url_base + str(bid) + '/' # comic url
        self._convert = convert # convert image according to extension
        self._convert_format = convert_format # force target format (e.g. 'jpg', 'png', 'webp'), None means follow inner extension
        self._page_delay = page_delay # delay between pages

        try:
            self._tunnel = f'https://{self._CHANNELS[tunnel]}{self._CHANNEL_BASE}' # set which image bed to use
        except:
            raise Exception('tunnel %d not exists' % tunnel)
        

        self._proxies = proxies # proxy host settings
        # if: proxy is on and proxy config is not null
        # check: proxy mode
        # check: proxy verify settings
        if proxies and proxy_config and (not proxy_config['mode'] in ['none', 'single', 'pool'] or not type(proxy_config['verify']) is bool):
            raise ValueError("invalid proxy config")
        
        self._proxy_config = proxy_config # proxy action settings
        self._skip_existed = skip_existed # skip existed file

        # initialize metadata
        self._load_book_metadata()


    def _load_book_metadata(self): # load book info
        try:
            res = None
            res = self._requests_get(self._url)
            res.raise_for_status()
        except:
            raise Exception(f"Network error: {res.status_code if res else 'OTHER'}")

        metadata = {}
        bs = bs4.BeautifulSoup(res.text, 'html.parser')

        # set title
        metadata['title'] = bs.select('.book-title h1')[0].text

        # author
        authors = []
        for author in bs.select('a[href^="/author"]'):
            authors.append(author.text)
        metadata['authors'] = authors

        # chapters
        section_titles = bs.select('.chapter>h4')
        chapter_sections = bs.select('.chapter-list')
        if not chapter_sections: # some pages store the chapter info in element's attr
            chapters_html = bs4.BeautifulSoup(lzstring.LZString().decompressFromBase64(bs.select('#__VIEWSTATE')[0].attrs.get('value')), 'html.parser')
            section_titles = chapters_html.select('h4')
            chapter_sections = chapters_html.select('.chapter-list')
        
        sections = []
        chapters = []
        for i, chapter_section in enumerate(chapter_sections):
            # run in each .chapter-list
            section_title = section_titles[i].text
            section_chapter_list = []
            for ul in chapter_section.select('ul'):
                # run in each .chapter-list ul
                chapter_list_in_one_ul = []
                for a in ul.select('a'):
                    # run in each .chapter-list ul li
                    chapter_list_in_one_ul.append({'chapter_name': a.attrs['title'], 'url': a.attrs['href']})

                chapter_list_in_one_ul.reverse()
                chapters += chapter_list_in_one_ul
                section_chapter_list += chapter_list_in_one_ul

            sections.append({'title': section_title, 'list': section_chapter_list})
        
        metadata['sections'] = sections
        metadata['chapters'] = chapters

        #save
        self.metadata = SimpleNamespace(**metadata)
        self.raw_metadata = metadata

    def write_metadata(self):
        chapter_folder = os.path.join(self._DOWNLOAD_FOLDER, self.metadata.title)
        os.makedirs(chapter_folder, exist_ok=True)
        with open(os.path.join(chapter_folder, 'config.json'), 'w', encoding='utf-8') as f:
            config = {
                'title': self.metadata.title,
                'authors': '、'.join(self.metadata.authors),
                'direction': 'rl',
                'language': 'zh',
                'folder': 'jpg',
                'width': 800,
                'height': 1280,
                'cover': '',
                'bid': self._bid,
            }
            json.dump(config, f, ensure_ascii=False, indent=4)

    #private functions
    def _requests_get(self, *args, **kwargs): # requests wrapper of switch proxy config
        if self._proxy_config['mode'] == 'single':
            return requests.get(*args, **kwargs, proxies=self._proxies[0], verify=self._proxy_config['verify'])
        elif self._proxy_config['mode'] == 'pool':
            return requests.get(*args, **kwargs, proxies=random.choice(self._proxies), verify=self._proxy_config['verify'])
        else:
            return requests.get(*args, **kwargs)

    def _get_chapter_struct(self, url): # load chapter info
        lz = lzstring.LZString()
        try:
            res = self._requests_get(url)
        except:
            raise Exception(f'Request failed: {url}')
        m = re.match(r'^.*\}\(\'(.*)\',(\d*),(\d*),\'([\w|\+|\/|=]*)\'.*$', res.text)
        return _packed(m.group(1), int(m.group(2)), int(m.group(3)), lz.decompressFromBase64(m.group(4)).split('|'))

    def _resolve_output_names(self, filename):
        # 拆解伺服器回傳 filename 嘅副檔名層級（例如 001.jpg.webp / 001.png.webp / 001.webp / 001.avif）
        stem, outer_ext = os.path.splitext(filename)
        inner_stem, inner_ext = os.path.splitext(stem)

        # 常見圖片副檔名對應 Pillow format
        ext_to_pil = {
            '.jpg': 'JPEG',
            '.jpeg': 'JPEG',
            '.png': 'PNG',
            '.webp': 'WEBP',
            '.gif': 'GIF',
            '.bmp': 'BMP',
            '.tiff': 'TIFF',
            '.tif': 'TIFF',
            '.avif': 'AVIF',
        }

        # 決定乾淨嘅主檔名（若為雙層圖片副檔名如 001.png.webp，主檔名為 001；否則為 stem）
        base_name = inner_stem if inner_ext.lower() in ext_to_pil else stem

        if self._convert_format:
            # 強行使用使用者指定嘅 format（支援 'jpg' 或 '.jpg'），忽略內層副檔名
            forced_ext = self._convert_format.strip().lower()
            if not forced_ext.startswith('.'):
                forced_ext = '.' + forced_ext
            target_ext = forced_ext
        elif inner_ext.lower() in ext_to_pil:
            # 雙層副檔名情況（例如 001.jpg.webp 或 001.png.webp）：跟隨內層副檔名轉換
            target_ext = inner_ext.lower()
        else:
            # 單層副檔名情況（例如 001.webp 或 001.avif）：convert=True 且未指定 format 時預設轉為 .jpg
            target_ext = '.jpg'

        pil_format = ext_to_pil.get(target_ext, target_ext.lstrip('.').upper())
        converted_filename = base_name + target_ext
        return converted_filename, pil_format

    def _download_chapter(self, url : str):
        # load chapter info
        chapter_info = self._get_chapter_struct(url)

        # get chapter name
        if not chapter_info:
            raise Exception(f"Failed to get metadata: {url}")

        # get chapter name
        chapter_name = chapter_info['cname']    # chapter name

        # replace iligal file path name in title and chapter name
        illegal_path_regex = r'[\\/:*?"<>|]'
        legal_path_bookname = re.sub(illegal_path_regex, '_', self.metadata.title)
        legal_path_chaptername = re.sub(illegal_path_regex, '_', chapter_name)

        # make dir
        rawfolder = os.path.join(self._DOWNLOAD_FOLDER, legal_path_bookname, 'raw', legal_path_chaptername)
        os.makedirs(rawfolder, exist_ok=True)

        # 依最終 convert format 決定整章節統一的轉檔資料夾名稱（預設為 'jpg'）
        convert_folder_name = 'jpg'
        if self._convert_format:
            convert_folder_name = self._convert_format.strip().lstrip('.').lower() or 'jpg'
        elif chapter_info.get('files'):
            first_converted_name, _ = self._resolve_output_names(chapter_info['files'][0])
            first_ext = os.path.splitext(first_converted_name)[1].lstrip('.').lower()
            if first_ext:
                convert_folder_name = first_ext

        jpgfolder = os.path.join(self._DOWNLOAD_FOLDER, legal_path_bookname, convert_folder_name, legal_path_chaptername)
        os.makedirs(jpgfolder, exist_ok=True)

        # get chapter info
        length = chapter_info['len']    # pages count
        e = chapter_info['sl']['e']     # verify code
        m = chapter_info['sl']['m']     # verify code
        path = chapter_info['path']     # image path

        print(f"下載 {self.metadata.title} {chapter_name} 中 共{length}頁")

        # download pages
        for i, filename in enumerate(chapter_info['files']):
            page_url = self._tunnel + path + filename

            # 根據 convert 設定決定檔名：
            # convert = True  -> raw 保留原始 filename，轉檔資料夾依副檔名或 convert_format 轉換輸出
            # convert = False -> 不寫死副檔名，直接保留伺服器回傳的原始 filename
            if self._convert:
                converted_filename, pil_format = self._resolve_output_names(filename)
                display_filename = converted_filename
            else:
                converted_filename, pil_format = None, None
                display_filename = filename

            yield (page_url, display_filename, length, i)
            
            success = False
            downloaded_new = False
            max_retry = 10
            for retry in range(max_retry):
                res = None
                try:
                    # rawfolder 永遠使用伺服器原始 filename，避免假設副檔名一定是 .webp
                    file_path = os.path.join(rawfolder, filename)
                    converted_path = os.path.join(jpgfolder, converted_filename) if self._convert else None

                    # 先檢查 raw 是否已存在：有 raw 就免重新下載；若開啟 skip_existed 且目標檔都齊就整頁跳過
                    raw_exists = os.path.isfile(file_path)
                    if self._skip_existed and raw_exists:
                        if not self._convert or os.path.isfile(converted_path):
                            success = True
                            break

                    # 只有在 raw 不存在（或未開啟 skip_existed）時才從伺服器下載
                    if not (self._skip_existed and raw_exists):
                        res = self._requests_get(page_url, params={'e': e, 'm': m}, headers=HTTP_HEADER, timeout=15)
                        res.raise_for_status()
                        
                        with open(file_path, 'wb') as f:
                            for chunk in res.iter_content(100000):
                                f.write(chunk)
                        downloaded_new = True
                            
                    if self._convert:
                        im = Image.open(file_path)
                        # 若目標格式為 JPEG 但原圖帶有透明通道（如 RGBA / P），先轉為 RGB 避免存檔報錯
                        if pil_format == 'JPEG' and im.mode in ('RGBA', 'LA', 'P'):
                            im = im.convert('RGB')
                        im.save(converted_path, pil_format)
                        
                    success = True
                    # 若只是用本地 raw 補轉檔而沒有發起網絡請求，可不用再額外 sleep page_delay
                    if not downloaded_new:
                        break
                    break
                except Exception as err:
                    status_code = res.status_code if res is not None else 'ERROR'
                    print(f"Page {display_filename} download failed: {status_code} Retry: ({retry + 1}/{max_retry})...")
                    
                    if retry == 2:
                        print("-> 偵測到 Token 可能過期，正在重新取得最新 e & m 參數...")
                        try:
                            fresh_info = self._get_chapter_struct(url)
                            if fresh_info and 'sl' in fresh_info:
                                e = fresh_info['sl']['e']
                                m = fresh_info['sl']['m']
                                path = fresh_info['path']
                                page_url = self._tunnel + path + filename
                                print("-> 成功更新 Token，繼續下載！")
                        except Exception as ref_err:
                            print(f"-> 更新 Token 失敗: {ref_err}")
                            
                    time.sleep(2)
            
            if not success:
                raise Exception(f'Network error: Failed to download page {display_filename}')
                
            if downloaded_new:
                time.sleep(self._page_delay)

    def download_chapter(self, index, callback=None, by='chapters', section=None):
        """Download a chapter, by index in chapters or section.chapters
        
        params:
            index : index of chapter
            callback : callback function during downloading, default is print progress
            by : download by chapters or sections
            section : section index, only used when by is sections
        """

        # default callback
        if callback is None:
            callback = lambda page_url, filename, total_page_count, current_page_number: print(f"{filename} ({current_page_number + 1}/{total_page_count})")
        
        # get url by index
        if by == 'chapters':
            chapter_url = self._host + self.metadata.chapters[index]['url']
        elif by == 'sections':
            try:
                chapter_url = self._host + self.metadata.sections[section]['list'][index]['url']
            except:
                raise Exception('Specific section error')
        else:
            raise Exception('Downloading index reference must be chapters or sections')
        
        # download chapter
        for progress in self._download_chapter(chapter_url):
            callback(*progress)

def convert_base(value, base):
    """Converts an integer to a string in any base"""
    d = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'

    if value == 0:
        return '0'

    result = ''
    while value > 0:
        remainder = value % base
        result = d[remainder] + result
        value //= base

    return result

def _packed(functionFrame, a, c, data): # parse meta data
    e = lambda innerC: ('' if innerC < a else e(int(innerC / a))) + (chr(innerC % a + 29) if innerC % a > 35 else convert_base(innerC % a, 36))
    c -= 1
    d = {}
    while c + 1:
        d[e(c)] = e(c) if data[c] == '' else data[c]
        c -= 1
    pieces = re.split(r'(\b\w+\b)', functionFrame)
    js = ''.join([d[x] if x in d else x for x in pieces]).replace('\\\'', '\'')
    return json.loads(re.search(r'^.*\((\{.*\})\).*$', js).group(1))
