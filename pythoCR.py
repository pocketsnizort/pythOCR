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
import pdb
from colorama import init, Fore, Style
import difflib
from itertools import product
from tqdm import tqdm
# from userconfig.userconfig import regex_replace, chars_to_try_to_replace, auto_same_sub_threshold, same_sub_threshold
from multiprocessing.dummy import Pool as ThreadPool 

version = "2.02"

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
        print(msg)
        user_input = input()
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
        substitutes = {word}
        for char, replacement in chars_to_try_to_replace:
            raw_subst = [filler(word, char, replacement) for word in substitutes]
            substitutes = set([subst for sublist in raw_subst for subst in sublist])
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
    
def new_ocr_image(arg_tuple):
    scene, language, pbar = arg_tuple
    img_path = scene[2]
    result_base = os.path.splitext(img_path)[0]
    
    tess_cmd = [args.tesseract_path, img_path, result_base, "-l", language, "-psm", "6", "hocr"]
    subprocess.call(tess_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # Read the content
    ext = ".hocr"
    if not os.path.exists(result_base + ext):
        ext = ".html"
    with open(result_base + ext, 'r', encoding="utf8") as ifile:
        html_content = ifile.read()
        
    # Convert to text only
    text = re.sub(r"<(?!/?em)[^>]+>", "", html_content)
    text = text.strip().replace("</em> <em>", " ").replace("&#39;", "'").replace("&quot;", "\"").replace("&amp;", "&").replace("&gt;", ">").replace("&lt;", "<")
    text = re.sub(r"<(/?)em>", "<\\1i>", text)
    text = '\n'.join([x.strip() for x in text.splitlines() if x.strip()])
    text = re.sub(r"</i>(?:\r\n|\n)<i>", "\n", text)
    
    pbar.update(1)
    return (text, (scene[0], scene[1]))

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
        ofile.write(u'\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV,'
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
    
    for idx in range(len(sub_data)):
        text = sub_data[idx][0]
        for regex in args.regex_replace:
            text = re.sub(regex[0], regex[1], text)
        sub_data[idx] = (text, sub_data[idx][1])
    
    if not args.no_spellcheck and len(args.heurist_char_replace) > 0:
        word_count = analyse_word_count(sub_data, args.lang)
        
        logging.debug("Correcting - Deleting heuristicly unwanted chars")
        sub_data = extreme_try_subs_without_char(sub_data, args.heurist_char_replace, args.lang, word_count)

    logging.debug("Correcting - Adding trailing frame")
    for idx in range(len(sub_data)):
        sub_data[idx] = (sub_data[idx][0], (sub_data[idx][1][0], str(int(sub_data[idx][1][1]) + 1)))
        
    logging.debug("Correcting - Merging identical consecutive lines")
    idx = 0
    while idx < len(sub_data) - 1:
        if int(sub_data[idx][1][1]) >= int(sub_data[idx + 1][1][0]):
            score = 100. * difflib.SequenceMatcher(None, strip_tags(sub_data[idx][0]), strip_tags(sub_data[idx + 1][0])).ratio()
            # score = 100. * (1. - (editdistance.eval(strip_tags(sub_data[idx][0]), strip_tags(sub_data[idx + 1][0])) / ((len(sub_data[idx][0]) + len(sub_data[idx + 1])) / 2)))
            a = sub_data[idx][0].replace('\n', "")
            b = sub_data[idx + 1][0].replace('\n', "")
            b = show_diff(difflib.SequenceMatcher(a=a, b=b))
            msg = "%s\n%s\nCompare score of %5.2f%%" % (a, b, score)
            if score >= args.auto_same_sub_threshold:
                logging.debug("\n%s - Approved (automatically - higher threshold)" % msg)
                sub_data[idx] = (score_lines(sub_data[idx][0], sub_data[idx + 1][0], args.lang), (sub_data[idx][1][0], sub_data[idx + 1][1][1]))
                del sub_data[idx + 1]
            elif score >= args.same_sub_threshold:
                if args.timid:
                    print(msg)
                    user_input = input("Approve similarity? (Y/n)").lower()
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
        
def new_filter_only(path, outputdir):
    logging.info("Starting to filter file %s" % path)
    vscmd = "\"%s\" -y -p --arg FichierSource=\"%s\" --arg dir=\"%s\" \"%s\" -" % (args.vapoursynth_path, os.path.abspath(path), os.path.abspath(outputdir), os.path.abspath(args.vpy))
    logging.debug("Command used: %s" % vscmd)
    with open(os.devnull, 'w') as fnull:
        subprocess.call(shlex.split(vscmd), stdout=fnull)
    
    if os.path.exists(path + ".ffindex"):
        os.remove(path + ".ffindex")
    
def get_scenes_from_scene_data(scene_data, last_frame, base_dir):
    scene_bounds = []
    scene_bounds = re.findall(r"(\d+),(\d),(\d),\"([^\"]*)\"", "\n".join(scene_data.split("\n")[1:]))
    scene_bounds = sorted(scene_bounds, key=lambda scene_bound: scene_bound[0])
    
    scenes = []
    start_frame = None
    start_img_path = None
    for idx, scene_bond in enumerate(scene_bounds):
        frame = int(scene_bond[0])
        is_start = int(scene_bond[1])
        is_end = int(scene_bond[2])
        img_path = scene_bond[3]
        img_path = os.path.join(base_dir, img_path)
        if idx == 0 and not is_start and is_end:
            # Case where scenechange missed first scene ??? (has happened)
            pass
        elif is_start and is_end: 
            # Case where the scene is one frame long (should not happen too often)
            scenes.append((frame, frame, img_path))
        elif is_start:
            start_frame = frame
            start_img_path = img_path
        elif is_end and start_frame and start_img_path:
            scenes.append((start_frame, frame, start_img_path))
            start_frame = None
            start_img_path = None
        else:
            # Should not get here often, but still
            pass
    if start_frame and start_img_path:
        scenes.append((start_frame, last_frame, start_img_path))
    return scenes
    
def ocr_scenes(scenes):
    logging.info("OCRing images")
    pool = ThreadPool(args.threads)
    pbar = tqdm(total=len(scenes), mininterval=1)
    scenes = pool.map(new_ocr_image, [(scene, args.lang, pbar) for scene in scenes])
    pool.close()
    pool.join()
    pbar.close()
    return scenes
    
def ocr_one_screenlog(screenlog_dir):
    logging.info("OCR - Processing directory %s" % screenlog_dir)

    with open(os.path.join(screenlog_dir, "SceneChanges.csv"), "r") as ifile:
        video_data, scene_data = ifile.read().split("[Scene Informations]\n", 1)
        
    global video_fps
    global last_frame
    
    video_data_match = re.findall(r"\[Video Informations\]\nfps=(\d+\.\d+)\nframe_count=(\d+)", video_data)[0]
    video_fps = float(video_data_match[0])
    last_frame = int(video_data_match[1]) - 1
    
    logging.debug("video framerate is %s" % str(video_fps))
    logging.debug("last frame is %s" % last_frame)
    
    scenes = get_scenes_from_scene_data(scene_data, last_frame, screenlog_dir)
    return ocr_scenes(scenes)
    
def new_ocr_only(input_root_dir):
    if not os.path.exists(os.path.join(input_root_dir, "default", "SceneChanges.csv")):
        logging.error("No screenlog found in dir \"%s\", aborting." % input_root_dir)
        return (None,)
    alt_exists = os.path.exists(os.path.join(input_root_dir, "alt", "SceneChanges.csv"))
    logging.debug("Alternative Screenlog found." if alt_exists else "No alternative Screenlog found.")
    
    if alt_exists:
        return (ocr_one_screenlog(os.path.join(input_root_dir, "default")), [("<font color=\"#ffff00\">" + text + "</font>", time) for (text, time) in ocr_one_screenlog(os.path.join(input_root_dir, "alt"))])
    else:
        return (ocr_one_screenlog(os.path.join(input_root_dir, "default")),)
    
def post_process_subs(subsdata, outputdir, path):
    # Merging everything and converting
    logging.info("Correcting subtitles") 
    sub_data = check_sub_data(subsdata[0])
    if len(subsdata) == 2:
        sub_data += check_sub_data(subsdata[1])
    logging.info("Converting to subtitle file") 
    sub_data = sorted(sub_data, key=lambda file: int(file[1][0]))
    {"ass": convert_to_ass, "srt": convert_to_srt}[args.sub_format](sub_data, os.path.join(outputdir, os.path.basename(path)))
    
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
    
def new_do_full(path):
    new_filter_only(path, args.workdir)
    subsdata = new_ocr_only(os.path.join(args.workdir, os.path.basename(path)))
    shutil.rmtree(os.path.join(args.workdir, os.path.basename(path)), ignore_errors=True)
    return subsdata
    
if __name__ == '__main__':
    default_ass_style = "Style: Default,Verdana,55.5,&H00FFFFFF,&H000000FF,&H00282828,&H00000000,-1,0,0,0,100.2,100,0,0,1,3.75,0,2,0,0,79,1"
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
    argparser.add_argument(
                '-d', '--delay', dest='delay', action="store_true",
                help='Delay correction after every video is processed')
    argparser.add_argument(
                '--tesseract-path', dest='tesseract_path', metavar='path to tesseract binary',
                type=str, default="tesseract",
                help='The path to user to call tesseract (default: tesseract)')
    argparser.add_argument(
                '--vapoursynth-path', dest='vapoursynth_path', metavar='path to vspipe binary',
                type=str, default="vspipe",
                help='The path to user to call vapoursynth (default: vspipe)')
    args = argparser.parse_args()

    if not os.path.exists(args.outputdir):
        os.makedirs(args.outputdir)
        logging.debug("The output directory didn't exist, it was created.")
        
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
    logging.debug("regex_replace list of %d is: %s" % (len(args.regex_replace), "; ".join(["\"%s\" to \"%s\"" % entry for entry in args.regex_replace])))
    logging.debug("heurist_char_replace list of %d is: %s" % (len(args.heurist_char_replace), "; ".join(["\"%s\" to \"%s\"" % entry for entry in args.heurist_char_replace])))
    init()
    
    if (args.mode == "full" or args.mode == "filter-only") and args.vpy is None:
        logging.error("You must provide a vpy file for the filter mode to work.")
        exit()
    
    logging.debug("Creating directory at path %s" % args.workdir)

    if not os.path.exists(args.workdir):
        os.makedirs(args.workdir)
    
    files_to_process = []
    for path in args.path:
        if os.path.isfile(path):
            if os.path.splitext(path)[1] in media_ext:
                files_to_process.append(path)
            else:
                logging.warning("%s is not a media video file" % path)
        elif os.path.isdir(path) and args.mode != "ocr":
            for file in os.listdir(path):
                if os.path.splitext(file)[1] in media_ext:
                    files_to_process.append(os.path.join(path, file))
        elif os.path.isdir(path) and args.mode == "ocr":
            if ("." + path.split(".")[-1]) in media_ext:
                files_to_process.append(path)
                    
    job = {"full": new_do_full, "ocr": new_ocr_only, "filter": new_filter_only}[args.mode]
                    
    subsdatalist = []
    for idx, file in enumerate(files_to_process):
        logging.info("Processing %s, file %d of %d" % (os.path.basename(file), idx + 1, len(files_to_process)))
        subsdata = job(file, args.outputdir) if args.mode == "filter" else job(file)
        if not args.mode == "filter" and not args.delay:
            post_process_subs(subsdata, args.outputdir, file)
        else:
            subsdatalist.append((subsdata, file))
             
    if not args.mode == "filter":
        for subsdata, path in subsdatalist:
            post_process_subs(subsdata, args.outputdir, path)
        


