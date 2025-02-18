# Copyright (c) Facebook, Inc. and its affiliates.
import argparse
import glob
import multiprocessing as mp
import os
import time
import cv2
from detectron2.structures.masks import BitMasks
from numpy.core.fromnumeric import sort
import tqdm
import torch
import time
import random
from detectron2.data.detection_utils import read_image
from detectron2.utils.logger import setup_logger

import numpy as np
from detectron2.data.catalog import MetadataCatalog
from detectron2.config import get_cfg
from detectron2.modeling import build_model
import detectron2.data.transforms as T
from detectron2.checkpoint import DetectionCheckpointer

from yolov7.config import add_yolo_config


from alfred.vis.image.mask import label2color_mask, vis_bitmasks, vis_bitmasks_with_classes
from alfred.vis.image.det import visualize_det_cv2_part, visualize_det_cv2_fancy

# constants
WINDOW_NAME = "COCO detections"


class DefaultPredictor:

    def __init__(self, cfg):
        self.cfg = cfg.clone()  # cfg can be modified by model
        self.model = build_model(self.cfg)
        self.model.eval()
        if len(cfg.DATASETS.TEST):
            self.metadata = MetadataCatalog.get(cfg.DATASETS.TEST[0])

        checkpointer = DetectionCheckpointer(self.model)
        checkpointer.load(cfg.MODEL.WEIGHTS)

        self.aug = T.ResizeShortestEdge(
            [cfg.INPUT.MIN_SIZE_TEST, cfg.INPUT.MIN_SIZE_TEST], cfg.INPUT.MAX_SIZE_TEST
        )

        self.input_format = cfg.INPUT.FORMAT
        assert self.input_format in ["RGB", "BGR"], self.input_format

    def __call__(self, original_image):
        with torch.no_grad():
            if self.input_format == "RGB":
                original_image = original_image[:, :, ::-1]
            height, width = original_image.shape[:2]
            image = self.aug.get_transform(
                original_image).apply_image(original_image)
            print('image after transform: ', image.shape)
            image = torch.as_tensor(image.astype("float32").transpose(2, 0, 1))
            inputs = {"image": image, "height": height, "width": width}
            tic = time.time()
            # predictions, pure_t = self.model([inputs])
            predictions = self.model([inputs])
            predictions = predictions[0]
            c = time.time() - tic
            print('cost: {}, fps: {}'.format(c, 1/c))
            return predictions


def setup_cfg(args):
    # load config from file and command-line arguments
    cfg = get_cfg()
    add_yolo_config(cfg)
    cfg.merge_from_file(args.config_file)
    cfg.merge_from_list(args.opts)

    cfg.MODEL.YOLO.CONF_THRESHOLD = args.confidence_threshold
    cfg.MODEL.YOLO.NMS_THRESHOLD = args.nms_threshold
    cfg.MODEL.YOLO.IGNORE_THRESHOLD = 0.1

    # cfg.INPUT.MIN_SIZE_TEST = 672  # 90ms
    # cfg.INPUT.MIN_SIZE_TEST = 2560  # 90ms
    # cfg.INPUT.MAX_SIZE_TEST = 3060  # 90ms
    cfg.INPUT.MAX_SIZE_TEST = 900  # 90ms
    # cfg.INPUT.MIN_SIZE_TEST = 512 # 70ms
    # cfg.INPUT.MIN_SIZE_TEST = 1080  # 40ms
    # cfg.INPUT.MAX_SIZE_TEST = 512 # 40ms
    # cfg.INPUT.MAX_SIZE_TEST = 1080  # 70ms
    cfg.freeze()
    return cfg


def get_parser():
    parser = argparse.ArgumentParser(
        description="Detectron2 demo for builtin configs")
    parser.add_argument(
        "--config-file",
        default="configs/quick_schedules/mask_rcnn_R_50_FPN_inference_acc_test.yaml",
        metavar="FILE",
        help="path to config file",
    )
    parser.add_argument("--webcam", action="store_true",
                        help="Take inputs from webcam.")
    parser.add_argument("--video-input", help="Path to video file.")
    parser.add_argument(
        "--input",
        # nargs="+",
        help="A list of space separated input images; "
        "or a single glob pattern such as 'directory/*.jpg'",
    )
    parser.add_argument(
        "--output",
        help="A file or directory to save output visualizations. "
        "If not given, will show output in an OpenCV window.",
    )

    parser.add_argument(
        '-c', "--confidence-threshold",
        type=float,
        default=0.21,
        help="Minimum score for instance predictions to be shown",
    )
    parser.add_argument(
        '-n', "--nms-threshold",
        type=float,
        default=0.6,
        help="Minimum score for instance predictions to be shown",
    )
    parser.add_argument(
        "--opts",
        help="Modify config options using the command-line 'KEY VALUE' pairs",
        default=[],
        nargs=argparse.REMAINDER,
    )
    return parser


def vis_res_fast(res, img, class_names, colors, thresh):
    ins = res['instances']
    bboxes = ins.pred_boxes.tensor.cpu().numpy()
    scores = ins.scores.cpu().numpy()
    clss = ins.pred_classes.cpu().numpy()

    if ins.has('pred_bit_masks'):
        # img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        # img = np.stack((img,)*3, axis=-1)

        bit_masks = ins.pred_bit_masks
        if isinstance(bit_masks, BitMasks):
            bit_masks = bit_masks.tensor.cpu().numpy()
        # img = vis_bitmasks_with_classes(img, clss, bit_masks)
        # img = vis_bitmasks_with_classes(img, clss, bit_masks, force_colors=colors, mask_border_color=(255, 255, 255), thickness=2)
        img = vis_bitmasks_with_classes(
            img, clss, bit_masks, force_colors=None, draw_contours=False)
        # img = vis_bitmasks(img, bit_masks, thickness=2, draw_contours=False)
        # img = vis_bitmasks(img, bit_masks, thickness=2, draw_contours=False, fill_mask=True)
    # print('img shape: ', img.shape)
    thickness = 1 if ins.has('pred_bit_masks') else 2
    font_scale = 0.3 if ins.has('pred_bit_masks') else 0.4
    img = visualize_det_cv2_part(
        img, scores, clss, bboxes, class_names=class_names, force_color=colors, line_thickness=thickness, font_scale=font_scale, thresh=thresh)
    # img = cv2.addWeighted(img, 0.9, m, 0.6, 0.9)
    return img


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    args = get_parser().parse_args()
    setup_logger(name="fvcore")
    logger = setup_logger()
    logger.info("Arguments: " + str(args))

    cfg = setup_cfg(args)

    metadata = MetadataCatalog.get(cfg.DATASETS.TEST[0])
    class_names = cfg.DATASETS.CLASS_NAMES
    predictor = DefaultPredictor(cfg)

    print(cfg.INPUT.MIN_SIZE_TEST, cfg.INPUT.MIN_SIZE_TEST, cfg.INPUT.MAX_SIZE_TEST)
    colors = [[random.randint(0, 255) for _ in range(3)]
              for _ in range(cfg.MODEL.YOLO.CLASSES)]
    conf_thresh = cfg.MODEL.YOLO.CONF_THRESHOLD
    print('confidence thresh: ', conf_thresh)

    if args.input:
        if os.path.isdir(args.input):
            imgs = glob.glob(os.path.join(args.input, '*.jpg'))
            imgs = sorted(imgs)
            for path in imgs:
                # use PIL, to be consistent with evaluation
                img = cv2.imread(path)
                print('ori img shape: ', img.shape)
                res = predictor(img)
                res = vis_res_fast(res, img, class_names, colors, conf_thresh)
                # cv2.imshow('frame', res)
                cv2.imshow('frame', res)
                if cv2.waitKey(0) & 0xFF == ord('q'):
                    break
        else:
            img = cv2.imread(args.input)
            res = predictor(img)
            res = vis_res_fast(res, img, class_names, colors, conf_thresh)
            # cv2.imshow('frame', res)
            cv2.imshow('frame', res)
            cv2.waitKey(0)
    elif args.webcam:
        print('Not supported.')
    elif args.video_input:
        video = cv2.VideoCapture(args.video_input)
        width = int(video.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(video.get(cv2.CAP_PROP_FRAME_HEIGHT))
        frames_per_second = video.get(cv2.CAP_PROP_FPS)
        num_frames = int(video.get(cv2.CAP_PROP_FRAME_COUNT))
        basename = os.path.basename(args.video_input)

        while(video.isOpened()):
            ret, frame = video.read()
            # frame = cv2.resize(frame, (640, 640))
            res = predictor(frame)
            res = vis_res_fast(res, frame, class_names, colors, conf_thresh)
            # cv2.imshow('frame', res)
            cv2.imshow('frame', res)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
