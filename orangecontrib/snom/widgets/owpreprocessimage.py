import time

from AnyQt.QtWidgets import QFormLayout

from Orange.data import Domain, DiscreteVariable, ContinuousVariable
from Orange.widgets.settings import DomainContextHandler
from Orange.widgets.utils.itemmodels import DomainModel
from orangewidget import gui
from orangewidget.settings import SettingProvider, ContextSetting, Setting

import Orange.data
from Orange import preprocess
from Orange.preprocess import Preprocess
from Orange.widgets.widget import Output

from orangecontrib.spectroscopy.preprocess import SelectColumn, \
    CommonDomain
from orangecontrib.spectroscopy.widgets.owhyper import ImagePlot

from orangecontrib.spectroscopy.widgets.owpreprocess import (
    GeneralPreprocess,
    create_preprocessor,
    InterruptException,
)
from orangecontrib.spectroscopy.widgets.preprocessors.registry import PreprocessorEditorRegistry
from orangecontrib.spectroscopy.widgets.preprocessors.utils import BaseEditorOrange
from orangecontrib.spectroscopy.widgets.gui import lineEditFloatRange
from orangewidget.widget import Msg


class AddFeature(SelectColumn):
    InheritEq = True


class _AddCommon(CommonDomain):

    def __init__(self, amount, domain):
        super().__init__(domain)
        self.amount = amount

    def transformed(self, data):
        return data.X + self.amount


class AddConstant(Preprocess):

    def __init__(self, amount=0.):
        self.amount = amount

    def __call__(self, data):
        common = _AddCommon(self.amount, data.domain)
        atts = [a.copy(compute_value=AddFeature(i, common))
                for i, a in enumerate(data.domain.attributes)]
        domain = Orange.data.Domain(atts, data.domain.class_vars,
                                    data.domain.metas)
        return data.from_table(domain, data)


class AddEditor(BaseEditorOrange):

    name = "Add constant"
    qualname = "orangecontrib.snom.add_constant_test"

    def __init__(self, parent=None, **kwargs):
        super().__init__(parent, **kwargs)

        self.amount = 0.

        form = QFormLayout()
        amounte = lineEditFloatRange(self, self, "amount", callback=self.edited.emit)
        form.addRow("Addition", amounte)
        self.controlArea.setLayout(form)

    def activateOptions(self):
        pass  # actions when user starts changing options

    def setParameters(self, params):
        self.amount = params.get("amount", 0.)

    @classmethod
    def createinstance(cls, params):
        params = dict(params)
        amount = float(params.get("amount", 0.))
        return AddConstant(amount=amount)

    def set_preview_data(self, data):
        if data:
            pass  # TODO any settings


preprocess_editors = PreprocessorEditorRegistry()
preprocess_editors.register(AddEditor, 100)


class AImagePlot(ImagePlot):
    def clear_markings(self):
        pass


class ImagePreviews:
    curveplot = SettingProvider(AImagePlot)
    curveplot_after = SettingProvider(AImagePlot)

    value_type = 1

    def __init__(self):
        # the name of curveplot is kept because GeneralPreprocess
        # expects these names
        self.curveplot = AImagePlot(self)
        self.curveplot_after = AImagePlot(self)

    def shutdown(self):
        self.curveplot.shutdown()
        self.curveplot_after.shutdown()


class SpectralImagePreprocess(GeneralPreprocess, ImagePreviews, openclass=True):
    def __init__(self):
        ImagePreviews.__init__(self)
        super().__init__()

    def onDeleteWidget(self):
        super().onDeleteWidget()
        ImagePreviews.shutdown(self)


class OWPreprocessImage(SpectralImagePreprocess):
    name = "Preprocess image"
    id = "orangecontrib.snom.widgets.preprocessimage"
    description = "Process image"
    icon = "icons/preprocessimage.svg"
    priority = 1010

    settings_version = 2

    settingsHandler = DomainContextHandler()

    _max_preview_spectra = 1000000
    preview_curves = Setting(10000)

    editor_registry = preprocess_editors
    BUTTON_ADD_LABEL = "Add preprocessor..."

    attr_value = ContextSetting(None)

    class Outputs:
        preprocessed_data = Output("Integrated Data", Orange.data.Table, default=True)
        preprocessor = Output("Preprocessor", preprocess.preprocess.Preprocess)

    class Warning(SpectralImagePreprocess.Warning):
        threshold_error = Msg("Low slider should be less than High")

    class Error(SpectralImagePreprocess.Error):
        image_too_big = Msg("Image for chosen features is too big ({} x {}).")

    class Information(SpectralImagePreprocess.Information):
        not_shown = Msg("Undefined positions: {} data point(s) are not shown.")

    def image_values(self):
        attr_value = self.attr_value.name if self.attr_value else None
        return lambda data, attr=attr_value: \
            data.transform(Domain([data.domain[attr]]))

    def image_values_fixed_levels(self):
        return None

    def __init__(self):
        self.markings_list = []
        super().__init__()

        self.feature_value_model = DomainModel(DomainModel.SEPARATED,
                                               valid_types=ContinuousVariable)
        self.feature_value = gui.comboBox(
            self.preview_settings_box, self, "attr_value",
            label="Show feature",
            contentsLength=12, searchable=True,
            callback=self.update_feature_value, model=self.feature_value_model)

        self.contextAboutToBeOpened.connect(lambda x: self.init_interface_data(x[0]))

        self.preview_runner.preview_updated.connect(self.redraw_data)

    def update_feature_value(self):
        self.redraw_data()

    def redraw_data(self):
        self.curveplot.update_view()
        self.curveplot_after.update_view()

    def init_interface_data(self, data):
        self.init_attr_values(data)
        self.curveplot.init_interface_data(data)
        self.curveplot_after.init_interface_data(data)

    def init_attr_values(self, data):
        domain = data.domain if data is not None else None
        self.feature_value_model.set_domain(domain)
        self.attr_value = (
            self.feature_value_model[0] if self.feature_value_model else None
        )

    def show_preview(self, show_info_anyway=False):
        super().show_preview(False)

    def create_outputs(self):
        self._reference_compat_warning()
        pp_def = [
            self.preprocessormodel.item(i)
            for i in range(self.preprocessormodel.rowCount())
        ]
        self.start(
            self.run_task,
            self.data,
            self.reference_data,
            pp_def,
            self.process_reference,
        )

    def set_data(self, data):
        super().set_data(data)

        self.closeContext()

        def valid_context(data):
            if data is None:
                return False
            annotation_features = [v for v in data.domain.metas + data.domain.class_vars
                                   if isinstance(v, (DiscreteVariable, ContinuousVariable))]
            return len(annotation_features) >= 1

        if valid_context(data):
            self.openContext(data)
        else:
            # to generate valid interface even if context was not loaded
            self.contextAboutToBeOpened.emit([data])

        self.curveplot.update_view()
        self.curveplot_after.update_view()

    @staticmethod
    def run_task(
        data: Orange.data.Table,
        reference: Orange.data.Table,
        pp_def,
        process_reference,
        state,
    ):
        def progress_interrupt(i: float):
            state.set_progress_value(i)
            if state.is_interruption_requested():
                raise InterruptException

        # Protects against running the task in succession many times, as would
        # happen when adding a preprocessor (there, commit() is called twice).
        # Wait 100 ms before processing - if a new task is started in meanwhile,
        # allow that is easily` cancelled.
        for _ in range(10):
            time.sleep(0.010)
            progress_interrupt(0)

        n = len(pp_def)
        plist = []
        for i in range(n):
            progress_interrupt(i / n * 100)
            item = pp_def[i]
            pp = create_preprocessor(item, reference)
            plist.append(pp)
            if data is not None:
                data = pp(data)
            progress_interrupt((i / n + 0.5 / n) * 100)
            if process_reference and reference is not None and i != n - 1:
                reference = pp(reference)
        # if there are no preprocessors, return None instead of an empty list
        preprocessor = preprocess.preprocess.PreprocessorList(plist) if plist else None
        return data, preprocessor


if __name__ == "__main__":  # pragma: no cover
    from Orange.widgets.utils.widgetpreview import WidgetPreview
    WidgetPreview(OWPreprocessImage).run(Orange.data.Table("whitelight.gsf"))