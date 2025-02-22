import json
import re
import tqdm
import cv2
import os
import glob
import shutil
import pandas as pd
import numpy as np
import multiprocessing
import matplotlib.image as mpimg
from collections import Counter
from nomeroff_net.tools import modelhub
from nomeroff_net.pipes.number_plate_text_readers.text_detector import TextDetector
from nomeroff_net.pipes.number_plate_localizators.yolo_v5_detector import Detector
from nomeroff_net.pipes.number_plate_keypoints_detectors.bbox_np_points import NpPointsCraft
from nomeroff_net.pipes.number_plate_classificators.options_detector import OptionsDetector

from nomeroff_net.tools.image_processing import (
    get_cv_zone_rgb,
    convert_cv_zones_rgb_to_bgr,
    reshape_points)


def option_checker(dataset_dir, img_format="png", part_size=1):
    options_detector = OptionsDetector()
    options_detector.load("latest")

    ann = "ann"
    img = "img"
    ann_dir = os.path.join(dataset_dir, ann)
    img_dir = os.path.join(dataset_dir, img)

    img_fnames = []
    notted_regions = []
    nottedcount_lines = []
    notted_states = []
    zones = []
    ann_fnames = []
    ann_data = []
    i = 0
    counter = Counter()
    for dir_name, subdir_list, file_list in os.walk(ann_dir):
        for fname in file_list:
            ann_path = os.path.join(ann_dir, fname)

            i += 1
            ann_fnames.append(ann_path)
            with open(ann_path) as jsonR:
                data = json.load(jsonR)
            img_name = data["name"]
            ann_data.append(data)

            img_path = os.path.join(img_dir, "{}.{}".format(img_name, img_format))
            zones.append(cv2.cvtColor(mpimg.imread(img_path), cv2.COLOR_RGB2BGR))

            img_fnames.append(img_path)
            notted_regions.append(data["region_id"])
            notted_states.append(data["state_id"])
            nottedcount_lines.append(data["count_lines"])
            if i >= part_size:
                # find standart
                region_ids, count_lines = options_detector.predict(zones)
                for (regionId, zone, nottedRegion, nottedState, imgFname,
                     annFname, annItem, nottedCountL, countL) in zip(region_ids,
                                                                     zones,
                                                                     notted_regions,
                                                                     notted_states,
                                                                     img_fnames,
                                                                     ann_fnames,
                                                                     ann_data,
                                                                     count_lines,
                                                                     nottedcount_lines):

                    # region
                    bad_region = False
                    if int(regionId) != int(nottedRegion):
                        bad_region = True
                        if not ('moderation' in annItem):
                            annItem['moderation'] = {}
                        annItem['moderation']['regionPredicted'] = int(regionId)
                        print("REGION NOT CORRECT IN {}".format(imgFname))
                        print("PREDICTED: {}".format(regionId))
                        print("ANNOTATED: {}".format(nottedRegion))
                        counter["BAD_REGION"] += 1
                    else:
                        counter["GOOD_REGION"] += 1

                    # count
                    bad_count = False
                    if int(countL) != int(nottedCountL):
                        bad_count = True
                        if not ('moderation' in annItem):
                            annItem['moderation'] = {}
                        annItem['moderation']['countPredicted'] = int(countL)
                        print("COUNT LINES NOT CORRECT IN {}".format(imgFname))
                        print("PREDICTED: {}".format(countL))
                        print("ANNOTATED: {}".format(nottedCountL))
                        counter["BAD_COUNT"] += 1
                    else:
                        counter["GOOD_COUNT"] += 1

                    if 'moderation' in annItem and (bad_region or bad_count):
                        with open(annFname, "w") as jsonW:
                            annItem['moderation']['isModerated'] = 0
                            json.dump(annItem, jsonW)

                img_fnames = []
                notted_regions = []
                nottedcount_lines = []
                notted_states = []
                zones = []
                ann_fnames = []
                ann_data = []
    return counter


def auto_number_grab(root_dir, csv_dataset_path, res_dir):
    detector = Detector()
    detector.load()

    np_points_craft = NpPointsCraft()
    np_points_craft.load()

    photos = pd.read_csv(csv_dataset_path)
    photos = photos.set_index(['photoId'])
    if os.path.exists(res_dir):
        shutil.rmtree(res_dir)
    os.makedirs(res_dir, exist_ok=False)

    for dir_name, subdir_list, file_list in os.walk(root_dir):
        for fname in file_list:
            baseName = os.path.splitext(os.path.basename(fname))[0]
            img_path = os.path.join(dir_name, fname)
            try:
                img = mpimg.imread(img_path)
            except IOError:
                print(f"[WARNING] {img_path} is not image")
                continue

            target_boxes = detector.predict(img)[0]

            images_points, images_mline_boxes = np_points_craft.detect([[img, target_boxes]])
            all_points = images_points[0]
            all_points = [ps for ps in all_points if len(ps)]

            # cut zones
            to_show_zones = [get_cv_zone_rgb(img, reshape_points(rect, 1)) for rect in all_points]
            zones = convert_cv_zones_rgb_to_bgr(to_show_zones)

            if len(to_show_zones) > 1:
                areas = [zone.shape[0] * zone.shape[1] for zone in zones]
                zone = to_show_zones[np.argmax(areas)]
            else:
                zone = to_show_zones[0]

            text = photos.loc[baseName]['npText']

            imgpath = os.path.join(res_dir, "./{}_{}.png".format(baseName, text))
            mpimg.imsave(imgpath, zone)


def delete_not_used_images_from_via_dataset(
        path_to_images,
        path_to_json,
        delete_not_used_images=True):
    with open(path_to_json) as ann:
        ann_data = json.load(ann)
    image_list = ann_data

    file_names = []
    for _id in tqdm.tqdm(image_list["_via_img_metadata"]):
        image_id = image_list["_via_img_metadata"][_id]["filename"]
        file_name = f'{path_to_images}/{image_id}'

        if not os.path.exists(file_name):
            print("[PROBLEM] Path", file_name, "not exists")
        file_names.append(file_name)
    print("[INFO] json file", path_to_json, "has", len(file_names), "annotated items")

    if not delete_not_used_images:
        return 0
    deleted_count = 0
    for file_name in glob.glob(f"{path_to_images}/*"):
        if file_name not in file_names and file_name != path_to_json:
            os.remove(file_name)
            deleted_count += 1
    print("[INFO] deleted not used images ", deleted_count)
    return 0


def check_ocr_model(root_dir,
                    model_path="latest",
                    text_detector_name="eu",
                    img_format="png",
                    predicted_part_size=1000,
                    replace_tamplate=None):
    if replace_tamplate is None:
        replace_tamplate = {'moderation': {'isModerated': 1, 'moderatedBy': 'ApelSYN'}}
    text_detector = TextDetector({
        text_detector_name: {
            "for_regions": [""],
            "model_path": model_path
        },
    })

    ann_dir = os.path.join(root_dir, "ann")
    jsons = []
    jsons_paths = []
    for dir_name, subdir_list, file_list in os.walk(ann_dir):
        for fname in file_list:
            fname = os.path.join(ann_dir, fname)
            jsons_paths.append(fname)
            with open(fname) as jsonF:
                jsonData = json.load(jsonF)
            jsons.append(jsonData)
    print("LOADED {} ANNOTATIONS".format(len(jsons)))

    img_dir = os.path.join(root_dir, "img")
    imgs = []
    for j in jsons:
        img_path =os.path.join(img_dir, "{}.{}".format(j["name"], img_format))
        img = cv2.imread(img_path)
        imgs.append(img)
    print("LOADED {} IMAGES".format(len(imgs)))

    predicted = []
    N = int(len(imgs) / predicted_part_size) + 1
    for i in range(N):
        part = i*predicted_part_size
        part_imgs = imgs[part:part+predicted_part_size]
        model_inputs = text_detector.preprocess(part_imgs, ["" for _ in part_imgs], [1 for _ in part_imgs])
        model_outputs = text_detector.forward(model_inputs)
        predicted_part = text_detector.postprocess(model_outputs)
        predicted += predicted_part

    print("PREDICTED {} IMAGES".format(len(predicted)))

    err_cnt = 0
    for i in range(len(jsons_paths)):
        json_path      = jsons_paths[i]
        predicted_item = predicted[i]
        jsonData       = jsons[i]
        jsonData["moderation"]["predicted"] = predicted_item
        if jsonData["description"] == jsonData["moderation"]["predicted"]:
            jsonData.update(replace_tamplate)
            jsonData["moderation"]["isModerated"] = 1
        else:
            print("Predicted '{}', real: '{}' in file {}".format(jsonData["moderation"]["predicted"],jsonData["description"], json_path))
            err_cnt = err_cnt+1

        with open(json_path, "w") as jsonWF:
            json.dump(jsonData, jsonWF)

    print("Error detection count: {}".format(err_cnt))
    print("Accuracy: {}".format(1-err_cnt/len(predicted)))


def get_datasets(names=None, states=None):
    if names is None:
        names = [
            "EuUaFrom2004",
            "EuUa1995",
            "Eu",
            "Ru",
            "Kz",
            "Ge",
            "By",
            "Su",
            "Kg",
            "Am",
        ]
    if states is None:
        states = [
            "train",
            "test",
            "val"
        ]

    datasets = {}
    for name in names:
        info = modelhub.download_dataset_for_model(name)
        print(name, info["dataset_path"])
        for state in states:
            datasets[(name, state)] = os.path.join(info["dataset_path"], state)
    return datasets


def read_json(fname):
    with open(fname) as jsonF:
        json_data = json.load(jsonF)
    return fname, json_data


def read_annotations(root_dir, processes=10):
    ann_dir = os.path.join(root_dir, "ann")
    jsons_paths = []
    for dir_name, subdir_list, file_list in os.walk(ann_dir):
        for fname in file_list:
            fname = os.path.join(ann_dir, fname)
            jsons_paths.append(fname)
    with multiprocessing.Pool(processes=processes) as pool:
        results = pool.map(read_json, jsons_paths)
    jsons = {}
    for (fname, json_data) in results:
        jsons[fname] = json_data
    return jsons


def find_all_datset_format(annotations):
    formats_counter = Counter()
    for fanme in annotations:
        json_data = annotations[fanme]
        numberplate_format = json_data["description"].lower()
        numberplate_format = re.sub(r"[0-9]", "#", numberplate_format)  # number
        numberplate_format = re.sub(r"[a-z]", "@", numberplate_format)  # letter
        numberplate_format = re.sub(r"[а-я]", "@", numberplate_format)  # letter
        numberplate_format = re.sub(r"[їіёъ]", "@", numberplate_format)  # letter
        formats_counter[numberplate_format] += 1
    return formats_counter.most_common()


def print_datset_format(annotations, ann_format):
    for fname in annotations:
        json_data = annotations[fname]
        numberplate_format = json_data["description"].lower()
        numberplate_format = re.sub(r"[0-9]", "#", numberplate_format)  # number
        numberplate_format = re.sub(r"[a-z]", "@", numberplate_format)  # letter
        numberplate_format = re.sub(r"[а-я]", "@", numberplate_format)  # letter
        numberplate_format = re.sub(r"[їіёъ]", "@", numberplate_format)  # letter
        if ann_format == numberplate_format:
            print("\t\t\t", fname, json_data)
            img_path = os.path.dirname(fname)
            img_path = img_path.replace("ann", "img")
            img_path = os.path.join(img_path, f"{json_data['name']}.png")
            img = cv2.imread(img_path)
            try:
                from matplotlib import pyplot as plt
                plt.imshow(img)
                plt.plot()
            except ModuleNotFoundError as _:
                pass
