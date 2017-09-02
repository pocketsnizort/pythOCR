import configargparse
import enchant
import logging
import multiprocessing
import os
import re
import json
import shlex
import shutil
import subprocess
from colorama import init, Fore, Style
import difflib
from itertools import product
from tqdm import tqdm
# from userconfig.userconfig import regex_replace, chars_to_try_to_replace, auto_same_sub_threshold, same_sub_threshold
from multiprocessing.dummy import Pool as ThreadPool 

version = "1.82"

media_ext = {".mp4", ".mkv", ".avi"}

last_frame = 0
video_fps = 0
   
def show_diff(seqm):
    """Unify operations between two compared strings
seqm is a difflib.SequenceMatcher instance whose a & b are strings"""
    output= []
    for opcode, a0, a1, b0, b1 in seqm.get_opcodes():
        if opcode == 'equal':
            output.append(seqm.a[a0:a1])
        elif opcode == 'insert':
            output.append(Fore.RED + Style.BRIGHT + seqm.b[b0:b1] + Style.RESET_ALL)
        elif opcode == 'delete':
            continue
        elif opcode == 'replace':
            output.append(Fore.RED + Style.BRIGHT + seqm.b[b0:b1] + Style.RESET_ALL)
    return ''.join(output)
   
def analyse_word_count(sub_data, language):
    word_count = dict()
    enchant_dict = enchant.Dict({"eng": "en_US", "fra": "fr_FR"}[language])
    for idx in range(0, len(sub_data)):
        for word in re.findall(r"\w+", strip_tags(sub_data[idx][0]), flags=re.UNICODE):
            if enchant_dict.check(word):
                try:
                    word_count[word] = word_count[word] + 1
                except KeyError:
                    word_count[word] = 1
    return word_count
   
def filler(word, from_char, to_char):
    options = [(c,) if c != from_char else (from_char, to_char) for c in word]
    return (''.join(o) for o in product(*options))
    
def user_input_replace_confirm(word, substitutes, fullstring):
    displaystring = fullstring.split(word, 1)
    displaystring = displaystring[0] + Fore.RED + Style.BRIGHT + word + Style.RESET_ALL + displaystring[1]
    msg = "Dialogue: \"%s\"\nBad word found, please select a substitute or enter [s] to skip:\n" % strip_tags(displaystring)
    msg += ", ".join([("\"%s\"[" + Fore.BLUE + Style.BRIGHT + "%d" + Style.RESET_ALL + "]") % (show_diff(difflib.SequenceMatcher(a=word, b=substitute[0])), idx + 1) for idx, substitute in enumerate(substitutes)])
    while True:
        user_input = input(msg)
        if user_input.lower() == "s":
            return word
        elif user_input == "":
            return substitutes[0][0]
        else:
            try: 
                idx = int(user_input)
            except ValueError:
                logging.warning("Please enter a valid number (not a number)")
                continue
            if idx >= 1 and idx <= len(substitutes):
                return substitutes[idx - 1][0]
            else:
                logging.warning("Please enter a valid number (out of bound)")
                continue
                
def extreme_try_word_without_char(word, fullstring, chars_to_try_to_replace, enchant_dict, word_count):
    if enchant_dict.check(word):
        return word
    else:
        substitutes = [word]
        for char, replacement in chars_to_try_to_replace:
            raw_subst = [filler(word, char, replacement) for word in substitutes]
            substitutes = [subst for sublist in raw_subst for subst in sublist]
        # Get a list of acceptable substitutes with their corresponding distance
        substitutes = [(substitute, difflib.SequenceMatcher(None, word, substitute).ratio()) for substitute in list(set(substitutes)) if enchant_dict.check(substitute)]
        if len(substitutes) > 0:
            logging.debug("Heuristic - Found bad word \"%s\", possibles substitutes are \"%s\"" % (word, str(substitutes)))
            if args.timid:
                return user_input_replace_confirm(word, substitutes, fullstring)
            else:
                chosen_subst = sorted(substitutes, key=lambda substitute: 100 * word_count.get(substitute[0], 0) * substitute[1], reverse=True)[0]
                logging.debug("Heuristic - Choose most likely substitute: %s [%d%%]" % chosen_subst)
            return chosen_subst[0]
        else:
            logging.debug("Heuristic - Found bad word \"%s\", no substitutes acceptable found" % word)
    return word
    
def extreme_try_string_without_char(string, chars_to_try_to_replace, enchant_dict, word_count):
    for word in re.findall(r"\w+[" + re.escape("".join([char[0] for char in chars_to_try_to_replace])) + r"]\w+", string, flags=re.UNICODE):
        substitute = extreme_try_word_without_char(word, string, chars_to_try_to_replace, enchant_dict, word_count)
        if substitute != word:
            re.sub(r"(\W)" + re.escape(word) + r"(\W)", "\\1" + substitute + "\\2", string, flags=re.UNICODE)
    return string
           
def extreme_try_subs_without_char(sub_data, chars_to_try_to_replace, language, word_count):
    enchant_dict = enchant.Dict({"eng": "en_US", "fra": "fr_FR"}[language])
    for idx in range(0, len(sub_data)):
        sub_data[idx] = (extreme_try_string_without_char(sub_data[idx][0], chars_to_try_to_replace, enchant_dict, word_count), sub_data[idx][1])
    return sub_data
    
def get_scene(screenlog_file_path):
    frames = []
    with open(screenlog_file_path) as ifile:
        lines = sorted(ifile, key=lambda line: int(line.strip().split(' ')[0]))
        for idx in range(0, len(lines)):
            frame_start, is_start, is_end  = lines[idx].strip().split(' ')
            if is_start == '1' and is_end == '1':
                #Scene change of only 1 frame
                frames.append([frame_start, frame_start])
                continue
            elif idx == 0 and is_end == '1':
                frames.append(('0', frame_start))
                continue
            if idx == len(lines) - 1:
                frame_end = last_frame
            else:
                frame_end = lines[idx + 1].strip().split(' ', 1)[0]          

            frames.append((frame_start, frame_end))
    return frames

def ocr_image(arg_tuple):
    # arg_tuple should be : (image_name, result_base, language, is_alt, pbar, args)
    args = arg_tuple[5]
    # OCR using tesseract
    tess_cmd = "tesseract \"%s\" \"%s\" -l %s -psm 6 hocr " % (arg_tuple[0], arg_tuple[1], arg_tuple[2])
    subprocess.call(shlex.split(tess_cmd), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # Read the content
    ext = ".hocr"
    if not os.path.exists(arg_tuple[1] + ext):
        ext = ".html"
    with open(arg_tuple[1] + ext, 'r', encoding="utf8") as ifile:
        html_content = ifile.read()
        
    # Convert to text only
    text = re.sub(r"<(?!/?em)[^>]+>", "", html_content)
    text = text.strip().replace("</em> <em>", " ").replace("&#39;", "'").replace("&quot;", "\"").replace("&amp;", "&").replace("&gt;", ">").replace("&lt;", "<")
    text = re.sub(r"<(/?)em>", "<\\1i>", text)
    text = '\n'.join([x.strip() for x in text.splitlines() if x.strip()])
    text = re.sub(r"</i>(?:\r\n|\n)<i>", "\n", text)
    for regex in args.regex_replace:
        text = re.sub(regex[0], regex[1], text)
    if arg_tuple[3] and text.strip():
        text = "<font color=\"#ffff00\">" + text + "</font>"
        
    arg_tuple[4].update(1)
    return text

def sec_to_time(secs):
    hours = secs / 3600
    minutes = (secs % 3600) / 60
    secs = secs % 60
    return "%02d:%02d:%05.2f" % (hours, minutes, secs)
                
def convert_to_srt(sub_data, mp4_path):
    # First, we need to handle the cas where default and alternative subs are displayed at the same time
    
    idx = 0
    while idx < len(sub_data) - 1:
        if int(sub_data[idx][1][1]) >= int(sub_data[idx + 1][1][0]):
            if "<font color=\"#ffff00\">" in sub_data[idx][0]:
                alt_line = sub_data[idx][0]
                def_line = sub_data[idx + 1][0]
            else:
                alt_line = sub_data[idx + 1][0]
                def_line = sub_data[idx][0]
                
            if int(sub_data[idx][1][1]) < int(sub_data[idx + 1][1][1]):
                # Case where first line shorter than the second
                bound1 = sub_data[idx + 1][1][0]
                bound2 = sub_data[idx][1][1]
                sub_data.insert(idx + 2, (sub_data[idx + 1][0], (bound2, sub_data[idx + 1][1][1])))
                sub_data[idx] = (sub_data[idx][0], (sub_data[idx][1][0], bound1))
                sub_data[idx + 1] = ("%s\n%s" % (alt_line, def_line), (bound1, bound2))
            elif int(sub_data[idx][1][1]) > int(sub_data[idx + 1][1][1]):
                # Case where first line longer than the second
                bound1 = sub_data[idx + 1][1][0]
                bound2 = sub_data[idx + 1][1][1]
                sub_data.insert(idx + 2, (sub_data[idx][0], (bound2, sub_data[idx][1][1])))
                sub_data[idx] = (sub_data[idx][0], (sub_data[idx][1][0], bound1))
                sub_data[idx + 1] = ("%s\n%s" % (alt_line, def_line), (bound1, bound2))
            else:
                # Case where the lines end at the same time
                sub_data[idx] = (sub_data[idx][0], (sub_data[idx][1][0], sub_data[idx + 1][1][0]))
                sub_data[idx + 1] = ("%s\n%s" % (alt_line, def_line), (sub_data[idx + 1][1][0], sub_data[idx + 1][1][1]))
            idx += 1
        idx += 1
        
        
    with open("%s.srt" % os.path.splitext(mp4_path)[0], "w", encoding="utf8") as ofile:
        idx = 1
        for data in sub_data:
            if len(data[0]) > 0:
                text = "%d\n" % idx
                text += ("%s --> %s\n" % (sec_to_time(float(data[1][0]) / video_fps), sec_to_time((float(data[1][1]) / video_fps)))).replace('.', ',')
                text += data[0]
                text += "\n\n"
                ofile.write(text)
                idx += 1
                
def convert_to_ass(sub_data, mp4_path):
    with open("%s.ass" % os.path.splitext(mp4_path)[0], "w", encoding="utf8") as ofile:
        ofile.write(u'[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\n'
                    u'PlayResX: 1920\nPlayResY: 1080\n\n')
        ofile.write(u'[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour,'
                    u' SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, '
                    u'StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, '
                    u'Alignment, MarginL, MarginR, MarginV, '
                    u'Encoding\n')
        ofile.write(args.ass_style)
        ofile.write(u'\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV,'
                    u' Effect, Text\n')
        for data in sub_data:
            if len(data[0]) > 0:
                starttime = sec_to_time(float(data[1][0]) / video_fps)
                endtime = sec_to_time((float(data[1][1]) / video_fps))
                text = data[0].replace("\n", "\\N").replace("<i>", "{\\i1}").replace("</i>", "{\\i0}").replace("<font color=\"#ffff00\">", "{\\an8}").replace("</font>", "{\\an}").replace("}{", "")
                ofile.write(u'Dialogue: 0,'+starttime+','+endtime+',Default,,0,0,0,,'+text+u'\n')
        
def score_lines(line_a, line_b, language):
    enchant_dict = enchant.Dict({"eng": "en_US", "fra": "fr_FR"}[language])
    score_a = sum([1 for word in re.findall(r"\w+", strip_tags(line_a), re.UNICODE) if enchant_dict.check(word)])
    score_b = sum([1 for word in re.findall(r"\w+", strip_tags(line_b), re.UNICODE) if enchant_dict.check(word)])
    if score_a > score_b:
        return line_a
    else:
        return line_b
        
def strip_tags(string):
    return string.replace('\n', ' ').replace('<i>', '').replace('</i>', '').replace('<font color="#ffff00">', '').replace('</font>', '')
   
def check_sub_data(sub_data):
    logging.debug("Correcting - Removing empty lines")
    sub_data = [data for data in sub_data if len(data[0]) > 0]
    
    if not args.no_spellcheck:
        word_count = analyse_word_count(sub_data, args.lang)
        
        logging.debug("Correcting - Deleting heuristicly unwanted chars")
        # sub_data = try_subs_without_char(sub_data, chars_to_try_to_replace, args.lang)
        sub_data = extreme_try_subs_without_char(sub_data, args.heurist_char_replace, args.lang, word_count)

    logging.debug("Correcting - Adding trailing frame")
    for data in sub_data:
        data = (data[0], (data[1][0], str(int(data[1][1]) + 1)))
        
    logging.debug("Correcting - Merging identical consecutive lines")
    idx = 0
    while idx < len(sub_data) - 1:
        if int(sub_data[idx][1][1]) >= int(sub_data[idx + 1][1][0]):
            score = 100. * difflib.SequenceMatcher(None, strip_tags(sub_data[idx][0]), strip_tags(sub_data[idx + 1][0])).ratio()
            # score = 100. * (1. - (editdistance.eval(strip_tags(sub_data[idx][0]), strip_tags(sub_data[idx + 1][0])) / ((len(sub_data[idx][0]) + len(sub_data[idx + 1])) / 2)))
            a = sub_data[idx][0].replace('\n', "")
            b = sub_data[idx + 1][0].replace('\n', "")
            b = show_diff(difflib.SequenceMatcher(a=a, b=b))
            msg = "%s\n%s\nCompare score of %06.2f" % (a, b, score)
            if score >= args.auto_same_sub_threshold:
                logging.debug("\n%s - Approved (automatically - higher threshold)" % msg)
                sub_data[idx] = (score_lines(sub_data[idx][0], sub_data[idx + 1][0], args.lang), (sub_data[idx][1][0], sub_data[idx + 1][1][1]))
                del sub_data[idx + 1]
            elif score >= args.same_sub_threshold:
                if args.timid:
                    user_input = input("%s Approve similarity? (Y/n)" % msg).lower()
                    logging.debug("User_input is %s" % user_input)
                    if user_input in ('y', ''):
                        logging.info("Change approved (user input)")
                        sub_data[idx] = (score_lines(sub_data[idx][0], sub_data[idx + 1][0], args.lang), (sub_data[idx][1][0], sub_data[idx + 1][1][1]))
                        del sub_data[idx + 1]
                elif not args.timid:
                    logging.debug("\n%s - Approved (automatically)" % msg)
                    sub_data[idx] = (score_lines(sub_data[idx][0], sub_data[idx + 1][0], args.lang), (sub_data[idx][1][0], sub_data[idx + 1][1][1]))
                    del sub_data[idx + 1]
        idx += 1
        
    return sub_data
    
def cleanup_make_dirs():
    if os.path.exists(screen_dir):
        shutil.rmtree(screen_dir, ignore_errors=True)
    os.makedirs(screen_dir)
        
    if os.path.exists(tess_dir):
        shutil.rmtree(tess_dir, ignore_errors=True)
    os.makedirs(tess_dir)
    
def filter_only(path, outputdir):
    if os.path.exists("SceneChanges.log"):
        os.remove("SceneChanges.log")
    if os.path.exists("SceneChangesAlt.log"):
        os.remove("SceneChangesAlt.log")

    logging.info("Starting to filter file %s" % path)
    vscmd = "vspipe -y --arg FichierSource=\"%s\" %s -" % (path, args.vpy)
    ffcmd = "ffmpeg -i - -c:v mpeg4 -qscale:v 3 -y \"%s\"" % (outputdir + "/" + os.path.basename(path))
    logging.debug("Command used: %s | %s" % (vscmd, ffcmd))
    vspipe_ps = subprocess.Popen(shlex.split(vscmd), stdout=subprocess.PIPE)
    subprocess.call(shlex.split(ffcmd), stdin=vspipe_ps.stdout)
    
    if os.path.exists("SceneChanges.log"):
        shutil.move("SceneChanges.log", outputdir + "/" + os.path.splitext(os.path.basename(path))[0] + ".log")
    if os.path.exists("SceneChangesAlt.log"):
        shutil.move("SceneChangesAlt.log", outputdir + "/" + os.path.splitext(os.path.basename(path))[0] + ".alt.log")
    
    if os.path.exists(path + ".ffindex"):
        os.remove(path + ".ffindex")
    
def ocr_only(path, outputdir):
    screenlog_dir = os.path.dirname(path)
    if screenlog_dir.strip() == "":
        screenlog_dir = '.'
    alt_exists = os.path.exists(screenlog_dir + "/" + os.path.splitext(os.path.basename(path))[0] + ".alt.log")
    logging.debug("Is there alts: %s" % str(alt_exists))

    logging.info("Using YoloCR in CLI mode.")
    logging.info("Prelude.")

    global video_fps
    global last_frame
    
    # Load Meta-Data (Framerate and frame count)
    video_fps = eval(re.findall(r'r_frame_rate="([^"]+)"', str(subprocess.check_output(shlex.split("ffprobe \"%s\" -v 0 -select_streams v:0 -print_format flat -show_entries stream=r_frame_rate" % path))))[0])
    logging.debug("video framerate is %s" % str(video_fps))

    last_frame = eval(re.findall(r'nb_read_frames="([^"]+)"', str(subprocess.check_output(shlex.split("ffprobe \"%s\" -v 0 -count_frames -select_streams v:0 -print_format flat -show_entries stream=nb_read_frames " % path))))[0] + "-1")
    logging.debug("last frame is %s" % last_frame)
    
    # Generating sub images from screenchange log
    frames = get_scene(os.path.splitext(screenlog_dir + "/" + os.path.basename(path))[0] + ".log")
    

    if alt_exists:
        # logging.debug("frames to extract are: %s" % str(frames))
        ffmpeg_cmd = "ffmpeg -loglevel error -i \"%s\" -vf select='%s',crop='h=ih/2:y=ih/2' -vsync 0 \"%s/%%0%dd.jpg\"" % (path, "+".join(["eq(n\,%s)" % scene[0] for scene in frames]), screen_dir, len(str(len(frames))))
        # logging.debug("ffmepg command is: %s" % ffmpeg_cmd)
        logging.info("Generating images")
        subprocess.call(shlex.split(ffmpeg_cmd), stdout=subprocess.DEVNULL)
        frames_alt = get_scene(os.path.splitext(screenlog_dir + "/" + os.path.basename(path))[0] + ".alt.log")
        # logging.debug("frames alt to extract are: %s" % str(frames_alt))
        ffmpeg_cmd = "ffmpeg -loglevel error -i \"%s\" -vf select='%s',crop='h=ih/2:y=0' -vsync 0 \"%s/%%0%dd_alt.jpg\"" % (path, "+".join(["eq(n\,%s)" % scene[0] for scene in frames_alt]), screen_dir, len(str(len(frames_alt))))
        # logging.debug("ffmepg alt command is: %s" % ffmpeg_cmd)
        logging.info("Generating alt images")
        subprocess.call(shlex.split(ffmpeg_cmd), stdout=subprocess.DEVNULL)
    else:
        # logging.debug("frames to extract are: %s" % str(frames))
        ffmpeg_cmd = "ffmpeg -loglevel error -i \"%s\" -vf select='%s' -vsync 0 \"%s/%%0%dd.jpg\"" % (path, "+".join(["eq(n\,%s)" % scene[0] for scene in frames]), screen_dir, len(str(len(frames))))
        # logging.debug("ffmepg command is: %s" % ffmpeg_cmd)
        logging.info("Generating images")
        subprocess.call(shlex.split(ffmpeg_cmd), stdout=subprocess.DEVNULL)
        
    # Parallele processing of sub images (OCR + converting to text + text fixing)
    length = len(frames)
    num_len = len(str(length))
    logging.info("OCRing images")
    pool = ThreadPool(args.threads)
    pbar = tqdm(total=length, mininterval=1)
    text_lines = pool.map(ocr_image, [("%s/%0*d.jpg" % (screen_dir, num_len, idx+1), "%s/%0*d" % (tess_dir, num_len, idx+1), args.lang, False, pbar, args) for idx in range(length)])
    sub_data = [(text_lines[idx], frames[idx]) for idx in range(length)]
    pool.close()
    pool.join()
    pbar.close()
    
    if alt_exists:
        logging.info("OCRing alt images")
        pool = ThreadPool(args.threads)
        length_alt = len(frames_alt)
        num_len_alt = len(str(length_alt))
        pbar = tqdm(total=length_alt, mininterval=1)
        text_lines_alt = pool.map(ocr_image, [("%s/%0*d_alt.jpg" % (screen_dir, num_len_alt, idx+1), "%s/%0*d_alt" % (tess_dir, num_len_alt, idx+1), args.lang, True, pbar, args) for idx in range(length_alt)])
        pool.close()
        pool.join()
        pbar.close()
        sub_data_alt = [(text_lines_alt[idx], frames_alt[idx]) for idx in range(length_alt)]
    
    # Merging everything and converting
    logging.info("Correcting subtitles") 
    sub_data = check_sub_data(sub_data)
    logging.info("Converting to subtitle file") 
    if alt_exists:
        sub_data += check_sub_data(sub_data_alt)
    sub_data = sorted(sub_data, key=lambda file: int(file[1][0]))
    {"ass": convert_to_ass, "srt": convert_to_srt}[args.sub_format](sub_data, outputdir + "/" + os.path.basename(path))
    
def type_regex_replace(string):
    try:
        with open(string, "r", encoding="utf8") as inputfile:
            json_str = inputfile.read()
        json_str = json.loads(json_str)
        return [(re.compile(entry["regex"]), entry["replace"]) for entry in json_str]
    except IOError:
        raise configargparse.ArgumentTypeError(" file \"%s\" not found" % string)
    
def type_heurist_char_replace(string):
    try:
        with open(string, "r", encoding="utf8") as inputfile:
            json_str = inputfile.read()
        json_str = json.loads(json_str)
        return [(entry["char"], entry["replace"]) for entry in json_str]
    except IOError:
        raise configargparse.ArgumentTypeError(" file \"%s\" not found" % string)
    
def do_full(path, outputdir):
    filter_only(path, args.workdir)
    ocr_only(args.workdir + "/" + os.path.basename(path), outputdir)
    os.remove(args.workdir + "/" + os.path.basename(path))
    os.remove(args.workdir + "/" + os.path.splitext(os.path.basename(path))[0] + ".log")
    if os.path.exists(args.workdir + "/" + os.path.splitext(os.path.basename(path))[0] + ".alt.log"):
        os.remove(args.workdir + "/" + os.path.splitext(os.path.basename(path))[0] + ".alt.log")
    
if __name__ == '__main__':
    default_ass_style = u"Style: Default,Verdana,55.5,&H00FFFFFF,&H000000FF,&H00282828,&H00000000,-1,0,0,0,100.2,100,0,0,1,3.75,0,2,0,0,79,1\n"
    argparser = configargparse.ArgumentParser(description='Process a previously filtered video and extract subtitles as srt.', prog="PythoCR")
    argparser.add_argument('--version', action='version', version='%(prog)s ' + version)
    argparser.add_argument(
                '-c', '--config', is_config_file=True,
                help='path to configuration file')
    argparser.add_argument('path', nargs='+', help='path to a filtered video')
    argparser.add_argument('-l', '--lang', dest='lang', metavar='language',
                           choices=['fra', 'eng'], default='fra',
                           help='Select the language of the subtitles (default: fra)')
    argparser.add_argument(
                '-wd', '--work-dir', dest='workdir', metavar='folder', type=str, default="temp",
                help='Directory where I will put all my temporary stuff (default ./temp)')
    argparser.add_argument(
                '-o', '--output-dir', dest='outputdir', metavar='folder', type=str, default="output",
                help='Directory where I will put all my released stuff (default ./output)')
    argparser.add_argument(
                '--log-level', dest='log_level', metavar='level',
                choices=['CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG'], default='INFO',
                help='Set the logging level (default INFO)')
    argparser.add_argument(
                '--ass-style', dest='ass_style', metavar='style',
                type=str, default=default_ass_style,
                help='Ass style to use if sub-format is ass (default: Verdana 60)')
    argparser.add_argument(
                '-rr', '--regex-replace', dest='regex_replace', metavar='path to regex-replace json',
                type=type_regex_replace, default=[],
                help='List of regex/replace for automatic correction')
    argparser.add_argument(
                '-hcr', '--heuristic-char-replace', dest='heurist_char_replace', metavar='char,replace',
                type=type_heurist_char_replace, default=[],
                help='List of char/replace for heuristic correction')
    argparser.add_argument(
                '--sub-format', dest='sub_format', metavar='format',
                choices=['srt', 'ass'], default='srt',
                help='Set the outputed subtitles format (default srt)')
    argparser.add_argument(
                '--mode', dest='mode', metavar='mode',
                choices=['full', 'filter', 'ocr'], default='full',
                help='Set the processing mode. "filter" to only start the filtering jobs, "ocr" to process already filtered videos, "full" for both. (default full)')
    argparser.add_argument(
                '--vpy', dest='vpy', metavar='vpy_file', type=str, default=None,
                help='vapoursynth file to use for filtering (required for "filter only" and "full" modes')
    argparser.add_argument(
                '--threads', dest='threads', metavar='number', type=int, default=multiprocessing.cpu_count(),
                help='Number of threads the script will use (default: automatic detection)')
    argparser.add_argument(
                '--auto-same-sub-threshold', dest='auto_same_sub_threshold', metavar='number', type=float, default=95.,
                help='Percentage of comparison to assert that two lines of subtitles are automatically the same (default: 95%%)')
    argparser.add_argument(
                '--same-sub-threshold', dest='same_sub_threshold', metavar='number', type=float, default=80.,
                help='Percentage of comparison to assert that two lines of subtitles are the same (default: 80%%)')
    argparser.add_argument(
                '--no-spellcheck', dest='no_spellcheck', action="store_true",
                help='Desactivate the function which tries to replace allegedly bad characters using spellcheck (it will make the "heurist_char_replace" option of the userconfig useless)')
    argparser.add_argument(
                '-t', '--timid', dest='timid', action="store_true",
                help='Activate timid mode (it will ask for user input when some corrections are not automatically approved)')
    args = argparser.parse_args()

    logging.basicConfig(
        level={'CRITICAL': 50,
         'ERROR': 40,
         'WARNING': 30,
         'INFO': 20,
         'DEBUG': 10}[args.log_level])
    logging.debug("Logger level set to %s" % args.log_level)
    logging.debug("Working with %d threads" % args.threads)
    logging.debug("Work directory set to %s" % args.workdir)
    logging.debug("Output directory set to %s" % args.outputdir)
    logging.debug("regex_replace list is: %s" % "; ".join(["\"%s\" to \"%s\"" % entry for entry in args.regex_replace]))
    logging.debug("heurist_char_replace list is: %s" % "; ".join(["\"%s\" to \"%s\"" % entry for entry in args.heurist_char_replace]))
    init()
    
    if (args.mode == "full" or args.mode == "filter-only") and args.vpy is None:
        logging.error("You must provide a vpy file for the filter mode to work.")
        exit()
    
    logging.debug("Creating directory at path %s" % args.workdir)

    screen_dir = "%s/%s" % (args.workdir, "screen_dir")
    tess_dir = "%s/%s" % (args.workdir, "TessResult")

    if not os.path.exists(args.workdir):
        os.makedirs(args.workdir)
    
    files_to_process = []
    
    for path in args.path:
        if os.path.isfile(path):
            if os.path.splitext(path)[1] in media_ext:
                files_to_process.append(path)
            else:
                logging.warning("%s is not a media video file" % path)
        elif os.path.isdir(path):
            for file in os.listdir(path):
                if os.path.splitext(file)[1] in media_ext:
                    files_to_process.append("%s/%s" % (path, file))
                    
    job = {"full": do_full, "ocr": ocr_only, "filter": filter_only}[args.mode]
                    
    for idx, file in enumerate(files_to_process):
        logging.info("Processing %s, file %d of %d" % (os.path.basename(file), idx + 1, len(files_to_process)))
        cleanup_make_dirs()
        job(file, args.outputdir)
        


