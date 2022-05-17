import sys
import os
import functools
import qimage2ndarray
import design
import math
import tarfile
import tifffile as tiff
import numpy as np
import cv2 as cv

from sentinel_downloader import download
from models.kumar_roy import KumarRoy64_10
from models.cloud_net import CloudNet
from sentinelhub import SHConfig, BBox, CRS

from PyQt5 import QtCore, QtGui, QtWebEngineWidgets, QtWebChannel
from PyQt5.QtWidgets import QDialog, QLineEdit, QDialogButtonBox, QLabel, QFormLayout, QGridLayout, QMainWindow, QMessageBox, QApplication

config = SHConfig()

class SignIn(QDialog):
    def __init__(self):
        super(SignIn, self).__init__()

        self.id = QLineEdit(self)
        self.secret = QLineEdit(self)
        self.resize(660, 100)
        self.setWindowTitle('Authorization to Sentinel Hub')

        buttonBox = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        label = QLabel(self)
        label.setText(
            "To use this application in online mode please enter yours sh_client_id and sh_client_secret from https://www.sentinel-hub.com/")

        layout = QFormLayout(self)
        layout.addWidget(label)
        layout.addRow("sh_client_id", self.id)
        layout.addRow("sh_client_secret", self.secret)
        layout.addWidget(buttonBox)

        buttonBox.accepted.connect(self.accept)
        buttonBox.rejected.connect(self.reject)

    def getIS(self):
        return self.id.text(), self.secret.text()


class Date(QDialog):
    def __init__(self, start, end):
        super(Date, self).__init__()
        self.setWindowTitle('Time period')
        buttonBox = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)

        label_s = QLabel(self)
        label_s.setText('Enter start date in format: YYYY-MM-DD')
        label_e = QLabel(self)
        label_e.setText('Enter end date in format: YYYY-MM-DD')

        self.start = QLineEdit(self)
        self.end = QLineEdit(self)
        if start:
            self.start.setText(start)
        if end:
            self.end.setText(end)

        layout = QGridLayout(self)
        layout.addWidget(label_s, 0, 0)
        layout.addWidget(label_e, 0, 1)
        layout.addWidget(self.start, 1, 0)
        layout.addWidget(self.end, 1, 1)
        layout.addWidget(buttonBox, 2, 0, 2, 0)

        buttonBox.accepted.connect(self.accept)
        buttonBox.rejected.connect(self.reject)

    def getDate(self):
        return self.start.text(), self.end.text()

class Resolution(QDialog):
    def __init__(self):
        super(Resolution, self).__init__()
        self.setWindowTitle('Resolution')
        buttonBox = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)

        label_r = QLabel(self)
        label_r.setText('Type resolution of image to download (how many meters does 1 pixel of image match)')

        self.res = QLineEdit(self)
        self.res.setText('60')

        layout = QGridLayout(self)
        layout.addWidget(label_r, 0, 0)
        layout.addWidget(self.res, 1, 0)
        layout.addWidget(buttonBox, 2, 0, 2, 0)

        buttonBox.accepted.connect(self.accept)
        buttonBox.rejected.connect(self.reject)

    def getRes(self):
        return self.res.text()


class SatelliteApp(QMainWindow, design.Ui_MainWindow):
    sh_client_id = ''
    sh_client_secret = ''
    start_date = None
    end_date = None
    image = None
    models = []
    labels = []
    runs = 0
    online = True

    def __init__(self):
        super().__init__()
        self.setupUi(self)

        # Models initialize
        # TODO use openvino
        # Костыль
        self.fire = KumarRoy64_10('C://Users//Никита//Desktop//fire//model.h5')
        self.cloud = CloudNet('C://Users//Никита//Desktop//cloud//model.h5')

        self.models.append(self.fire)
        self.models.append(self.cloud)

        # Map
        # TODO May be it's better to make map able to use satellite vision
        # it can be done only with google map + openstreent map feature
        # see https://stackoverflow.com/questions/50672846/how-to-load-a-google-maps-baselayer-in-leaflet-after-june-2018
        self.view = QtWebEngineWidgets.QWebEngineView()
        self.channel = QtWebChannel.QWebChannel()
        self.channel.registerObject("SatelliteApp", self)
        self.view.page().setWebChannel(self.channel)

        file = os.path.join(
            os.path.dirname(os.path.realpath(__file__)),
            "assets/map.html",
        )
        self.view.setUrl(QtCore.QUrl.fromLocalFile(file))
        self.gridLayout_2.addWidget(self.view)
        self.num_markers = 0
        self.markers = [(), ()]

        # Map Properties

        self.button_mode.clicked.connect(self.mode)
        self.button_clear.clicked.connect(self.clear_marker)

        # Buttons

        self.button_analyze.clicked.connect(self.analyze)
        self.button_save.clicked.connect(self.save)
        self.button_exit.clicked.connect(self.exit)

        # Results

        self.labels.append(self.label_fire)
        self.labels.append(self.label_cloud)

        # Sign in

        self.sign_in = SignIn()

    # Map

    @QtCore.pyqtSlot(float, float, result=int)
    def addMarker(self, lng, lat):
        if self.num_markers == 2:
            return 0
        self.num_markers += 1
        self.markers[self.num_markers-1] = (lng, lat)
        return self.num_markers

    def norm_markers(self, x, y):
        # 0 - longitude, 1 - lattitude
        ll = (min(x[0], y[0]), min(x[1], y[1]))
        ur = (max(x[0], y[0]), max(x[1], y[1]))
        return ll, ur

    # Map Properties

    def mode(self):
        if self.online == True:
            self.online = False
            self.button_mode.setText("Online mode")
            # TODO implement ofline mode: choosing images on computer
        else:
            self.online = True
            self.button_mode.setText("Offline mode")

    def clear_marker(self):
        if self.num_markers == 2:
            self.markers[1] = (None, None)
            self.num_markers = 1
            self.view.page().runJavaScript("map.removeLayer(marker_2);")
        elif self.num_markers == 1:
            self.markers[0] = (None, None)
            self.num_markers = 0
            self.view.page().runJavaScript("map.removeLayer(marker_1);")

    # Buttons

    def analyze(self):
        if self.online == True:
            if self.num_markers != 2:
                self.view.page().runJavaScript("alert_markers();")
                return
            result = self.authorize()
            if result == False:
                return
            self.date = Date(self.start_date, self.end_date)
            self.date.exec_()
            self.start_date, self.end_date = self.date.getDate()

            ll, ur = self.norm_markers(self.markers[0], self.markers[1])
            bbox = (ll[0], ll[1], ur[0], ur[1])
            bbox = BBox(bbox=bbox, crs=CRS.WGS84)
            self.res = Resolution()
            self.res.exec_()
            resolution = int(self.res.getRes())
            print('Start downloading')
            image_path = download(bbox, resolution, self.start_date, self.end_date, config)
            self.image = tiff.imread(image_path)
        else:
            # TODO implement ofline mode: choosing images on computer
            # Костыль
            self.image = tiff.imread(
                'C://Users//Никита//Desktop//fire//image.tif')
        print(self.image.shape)
        print('Start processing')
        # TODO handle downloading pieces of choosen area
        i = 0
        for model in self.models:
            res = model.process(self.image)
            res = qimage2ndarray.array2qimage(res)
            self.labels[i].setPixmap(QtGui.QPixmap.fromImage(res))
            i += 1
        # TODO remove if map can use satellite vision or display on gui
        cv.imshow('Image', cv.resize(np.concatenate(
            (self.image[:, :, 3:4], self.image[:, :, 2:3], self.image[:, :, 1:2]), axis=2), (600, 600), interpolation=cv.INTER_AREA))
        cv.waitKey(0)

    def save(self):
        i = 0
        for label in self.labels:
            img = qimage2ndarray.rgb_view(label.pixmap().toImage())
            cv.imwrite('res_{}_model_{}.png'.format(self.models[i].get_type(), self.runs),
                       cv.cvtColor(img, cv.COLOR_RGB2BGR))
            i += 1
        self.runs += 1

    def exit(self):
        reply = QMessageBox.question(self, 'Message', 'Are you sure to exit?',
                                     QMessageBox.Yes | QMessageBox.No,
                                     QMessageBox.No)
        if reply == QMessageBox.Yes:
            if self.ee:
                self.ee.logout()
                self.api.logout()
            self.close()

    # Sign in

    def authorize(self):
        if not config.sh_client_id or not config.sh_client_secret:
            self.sign_in.exec_()
            self.sh_client_id, self.sh_client_secret = self.sign_in.getIS()
            if not self.sh_client_id or not self.sh_client_secret:
                return False
            config.sh_client_id = self.sh_client_id
            config.sh_client_secret = self.sh_client_secret
            config.save()
        return True


def main():
    app = QApplication(sys.argv)
    window = SatelliteApp()
    window.show()
    app.exec_()


if __name__ == '__main__':
    main()
