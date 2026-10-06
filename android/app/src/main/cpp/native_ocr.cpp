// Mobile OCR pipeline. Models/configuration remain owned by RapidOCR.
// Architecture reference: RapidAI/RapidOcrAndroidOnnx (Apache-2.0).
#include <jni.h>
#include <onnxruntime_cxx_api.h>
#include <opencv2/core.hpp>
#include <opencv2/imgproc.hpp>
#include <algorithm>
#include <array>
#include <cmath>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
using Box = std::array<cv::Point2f, 4>;
Ort::Env& environment() {
    static Ort::Env env(ORT_LOGGING_LEVEL_WARNING, "lvjiang-native");
    return env;
}

class Model {
    Ort::Session session_;
    std::string input_, output_;
public:
    size_t inputBytes = 0, outputBytes = 0;
    int runs = 0, maxHeight = 0, maxWidth = 0;
    Model(const std::string& path, int threads) : session_(nullptr) {
        Ort::SessionOptions options;
        options.DisableCpuMemArena();
        options.SetIntraOpNumThreads(threads);
        options.SetInterOpNumThreads(1);
        options.AddConfigEntry("session.intra_op.allow_spinning", "0");
        options.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_ENABLE_ALL);
        session_ = Ort::Session(environment(), path.c_str(), options);
        Ort::AllocatorWithDefaultOptions allocator;
        input_ = session_.GetInputNameAllocated(0, allocator).get();
        output_ = session_.GetOutputNameAllocated(0, allocator).get();
    }
    Ort::Value infer(std::vector<float>& input, int height, int width) {
        inputBytes = std::max(inputBytes, input.size() * sizeof(float));
        maxHeight = std::max(maxHeight, height);
        maxWidth = std::max(maxWidth, width);
        ++runs;
        std::array<int64_t, 4> shape{1, 3, height, width};
        auto memory = Ort::MemoryInfo::CreateCpu(OrtDeviceAllocator, OrtMemTypeDefault);
        auto tensor = Ort::Value::CreateTensor<float>(
            memory, input.data(), input.size(), shape.data(), shape.size());
        const char* inputs[]{input_.c_str()};
        const char* outputs[]{output_.c_str()};
        auto result = session_.Run(Ort::RunOptions{nullptr}, inputs, &tensor, 1, outputs, 1);
        outputBytes = std::max(outputBytes,
            result[0].GetTensorTypeAndShapeInfo().GetElementCount() * sizeof(float));
        return std::move(result[0]);
    }
    void resetStats() { inputBytes = outputBytes = 0; runs = maxHeight = maxWidth = 0; }
    std::string stats() const {
        std::ostringstream out;
        out << "{\"runs\":" << runs << ",\"max_input_bytes\":" << inputBytes
            << ",\"max_output_bytes\":" << outputBytes
            << ",\"max_height\":" << maxHeight << ",\"max_width\":" << maxWidth << '}';
        return out.str();
    }
    std::vector<std::string> characters() {
        Ort::AllocatorWithDefaultOptions allocator;
        auto value = session_.GetModelMetadata().LookupCustomMetadataMapAllocated("character", allocator);
        if (!value) throw std::runtime_error("OCR model has no character metadata");
        std::istringstream stream(value.get());
        std::vector<std::string> result{""}; // CTC blank
        std::string line;
        while (std::getline(stream, line)) {
            if (!line.empty() && line.back() == '\r') line.pop_back();
            result.push_back(line);
        }
        result.emplace_back(" ");
        return result;
    }
};

struct Config {
    std::array<int, 12> i;
    std::array<float, 5> f;
};

// Single float32 CHW allocation, no HWC float64 array or Java tensor copy.
std::vector<float> normalize(const cv::Mat& image, int paddedWidth = 0) {
    int width = std::max(image.cols, paddedWidth);
    size_t plane = static_cast<size_t>(width) * image.rows;
    std::vector<float> result(plane * 3, 0.f);
    for (int y = 0; y < image.rows; ++y) {
        const auto* row = image.ptr<cv::Vec3b>(y);
        for (int x = 0; x < image.cols; ++x) {
            for (int channel = 0; channel < 3; ++channel) {
                result[channel * plane + y * width + x] =
                    (static_cast<float>(row[x][channel]) / 255.f - .5f) / .5f;
            }
        }
    }
    return result;
}

Box ordered(const cv::RotatedRect& rect) {
    Box points;
    rect.points(points.data());
    std::stable_sort(points.begin(), points.end(),
                     [](auto a, auto b) { return a.x < b.x; });
    auto left = std::minmax(points[0], points[1],
                           [](auto a, auto b) { return a.y < b.y; });
    auto right = std::minmax(points[2], points[3],
                            [](auto a, auto b) { return a.y < b.y; });
    return {left.first, right.first, right.second, left.second};
}

double score(const cv::Mat& probability, const Box& box) {
    float left = box[0].x, right = left, top = box[0].y, bottom = top;
    for (auto p : box) {
        left = std::min(left, p.x); right = std::max(right, p.x);
        top = std::min(top, p.y); bottom = std::max(bottom, p.y);
    }
    int x1 = std::clamp(static_cast<int>(std::floor(left)), 0, probability.cols - 1);
    int x2 = std::clamp(static_cast<int>(std::ceil(right)), 0, probability.cols - 1);
    int y1 = std::clamp(static_cast<int>(std::floor(top)), 0, probability.rows - 1);
    int y2 = std::clamp(static_cast<int>(std::ceil(bottom)), 0, probability.rows - 1);
    cv::Mat mask = cv::Mat::zeros(y2 - y1 + 1, x2 - x1 + 1, CV_8U);
    std::vector<cv::Point> polygon;
    for (auto p : box) polygon.emplace_back(static_cast<int>(p.x - x1), static_cast<int>(p.y - y1));
    cv::fillPoly(mask, std::vector<std::vector<cv::Point>>{polygon}, cv::Scalar(1));
    return cv::mean(probability(cv::Rect(x1, y1, mask.cols, mask.rows)), mask)[0];
}

Box expand(const Box& box, float ratio) {
    double twiceArea = 0, length = 0;
    std::vector<cv::Point2f> truncated;
    for (size_t n = 0; n < box.size(); ++n) {
        auto a = box[n], b = box[(n + 1) % box.size()];
        twiceArea += static_cast<double>(a.x) * b.y - static_cast<double>(a.y) * b.x;
        length += std::hypot(static_cast<double>(a.x) - b.x, static_cast<double>(a.y) - b.y);
        truncated.emplace_back(static_cast<int>(a.x), static_cast<int>(a.y));
    }
    if (length <= 0) throw std::runtime_error("Invalid OCR detection polygon");
    float distance = static_cast<float>(std::abs(twiceArea) * .5 * ratio / length);
    auto rectangle = cv::minAreaRect(truncated);
    rectangle.size.width += 2 * distance;
    rectangle.size.height += 2 * distance;
    Box expanded;
    rectangle.points(expanded.data());
    std::vector<cv::Point2f> rounded;
    for (auto p : expanded) rounded.emplace_back(std::nearbyint(p.x), std::nearbyint(p.y));
    return ordered(cv::minAreaRect(rounded));
}

int aligned(double dimension) {
    return std::max(32, static_cast<int>(std::nearbyint(static_cast<int>(dimension) / 32.)) * 32);
}

class Engine {
    Config config_;
    Model detector_, classifier_, recognizer_;
    std::vector<std::string> characters_;

    std::vector<Box> detect(const cv::Mat& image) {
        double scale = std::max(1., static_cast<double>(config_.i[4]) / std::min(image.rows, image.cols));
        int width = aligned(image.cols * scale), height = aligned(image.rows * scale);
        // Bound the final detector tensor, including short-side enlargement.
        double pixels = static_cast<double>(width) * height;
        if (pixels > config_.i[11]) {
            double reduction = std::sqrt(config_.i[11] / pixels);
            width = std::max(32, static_cast<int>(width * reduction / 32) * 32);
            height = std::max(32, static_cast<int>(height * reduction / 32) * 32);
        }
        cv::Mat resized;
        cv::resize(image, resized, cv::Size(width, height));
        auto input = normalize(resized);
        auto output = detector_.infer(input, height, width);
        auto shape = output.GetTensorTypeAndShapeInfo().GetShape();
        if (shape.size() != 4 || shape[0] != 1 || shape[1] != 1)
            throw std::runtime_error("Unexpected OCR detector output shape");
        cv::Mat probability(static_cast<int>(shape[2]), static_cast<int>(shape[3]),
                            CV_32F, output.GetTensorMutableData<float>());
        cv::Mat mask;
        cv::compare(probability, config_.f[0], mask, cv::CMP_GT);
        if (config_.i[6]) cv::dilate(mask, mask, cv::Mat::ones(2, 2, CV_8U));
        std::vector<std::vector<cv::Point>> contours;
        cv::findContours(mask, contours, cv::RETR_LIST, cv::CHAIN_APPROX_SIMPLE);
        std::vector<Box> boxes;
        const size_t count = std::min(contours.size(), static_cast<size_t>(config_.i[5]));
        for (size_t n = 0; n < count; ++n) {
            auto rectangle = cv::minAreaRect(contours[n]);
            if (std::min(rectangle.size.width, rectangle.size.height) < 3) continue;
            auto box = ordered(rectangle);
            if (score(probability, box) < config_.f[1]) continue;
            box = expand(box, config_.f[2]);
            auto expanded = cv::minAreaRect(std::vector<cv::Point2f>(box.begin(), box.end()));
            if (std::min(expanded.size.width, expanded.size.height) < 5) continue;
            for (auto& p : box) {
                p.x = std::clamp(static_cast<float>(std::nearbyint(
                    p.x / probability.cols * image.cols)), 0.f, static_cast<float>(image.cols - 1));
                p.y = std::clamp(static_cast<float>(std::nearbyint(
                    p.y / probability.rows * image.rows)), 0.f, static_cast<float>(image.rows - 1));
            }
            if (cv::norm(box[0] - box[1]) > 3 && cv::norm(box[0] - box[3]) > 3) boxes.push_back(box);
        }
        std::stable_sort(boxes.begin(), boxes.end(), [](const Box& a, const Box& b) {
            return a[0].y != b[0].y ? a[0].y < b[0].y : a[0].x < b[0].x;
        });
        for (size_t n = 1; n < boxes.size(); ++n) {
            for (size_t j = n; j > 0; --j) {
                if (std::abs(boxes[j][0].y - boxes[j-1][0].y) < 10 &&
                    boxes[j][0].x < boxes[j-1][0].x) std::swap(boxes[j], boxes[j-1]);
                else break;
            }
        }
        return boxes; // output and detector input released before recognition.
    }

    cv::Mat crop(const cv::Mat& image, const Box& box) {
        int width = static_cast<int>(std::max(cv::norm(box[0]-box[1]), cv::norm(box[2]-box[3])));
        int height = static_cast<int>(std::max(cv::norm(box[0]-box[3]), cv::norm(box[1]-box[2])));
        Box destination{cv::Point2f(0,0), cv::Point2f(width,0),
                        cv::Point2f(width,height), cv::Point2f(0,height)};
        auto transform = cv::getPerspectiveTransform(box.data(), destination.data());
        cv::Mat result;
        cv::warpPerspective(image, result, transform, cv::Size(width,height),
                            cv::INTER_CUBIC, cv::BORDER_REPLICATE);
        if (height >= width * 1.5) cv::rotate(result, result, cv::ROTATE_90_COUNTERCLOCKWISE);
        return result;
    }

    void classify(cv::Mat& image) {
        int height = config_.i[7], width = config_.i[8];
        int resizedWidth = std::min(width, static_cast<int>(std::ceil(height * image.cols / double(image.rows))));
        cv::Mat resized;
        cv::resize(image, resized, cv::Size(resizedWidth, height));
        auto input = normalize(resized, width);
        auto output = classifier_.infer(input, height, width);
        if (output.GetTensorTypeAndShapeInfo().GetElementCount() != 2)
            throw std::runtime_error("Unexpected OCR classifier output shape");
        auto values = output.GetTensorData<float>();
        if (values[1] > values[0] && values[1] > config_.f[4])
            cv::rotate(image, image, cv::ROTATE_180);
    }

    std::pair<std::string, float> recognize(const cv::Mat& image) {
        int height = config_.i[9];
        double ratio = image.cols / double(image.rows);
        int width = std::max(config_.i[10], static_cast<int>(height * ratio));
        int resizedWidth = std::min(width, static_cast<int>(std::ceil(height * ratio)));
        cv::Mat resized;
        cv::resize(image, resized, cv::Size(resizedWidth, height));
        auto input = normalize(resized, width);
        auto output = recognizer_.infer(input, height, width);
        auto shape = output.GetTensorTypeAndShapeInfo().GetShape();
        if (shape.size() != 3 || shape[0] != 1 || shape[2] != static_cast<int64_t>(characters_.size()))
            throw std::runtime_error("OCR recognizer character/output mismatch");
        auto values = output.GetTensorData<float>();
        std::string text;
        double scoreSum = 0;
        int selected = 0, previous = -1;
        for (int64_t t = 0; t < shape[1]; ++t) {
            const float* row = values + t * shape[2];
            int best = static_cast<int>(std::max_element(row, row + shape[2]) - row);
            if (best != 0 && best != previous) {
                text += characters_[best];
                scoreSum += row[best];
                ++selected;
            }
            previous = best;
        }
        return {text, selected ? static_cast<float>(scoreSum / selected) : 0.f};
    }

    static std::string quote(const std::string& text) {
        std::ostringstream out;
        out << '"';
        for (unsigned char ch : text) {
            if (ch == '"' || ch == '\\') out << '\\' << ch;
            else if (ch < 32) {
                const char* hex = "0123456789abcdef";
                out << "\\u00" << hex[ch >> 4] << hex[ch & 15];
            } else out << ch;
        }
        out << '"';
        return out.str();
    }

public:
    Engine(const std::string& det, const std::string& cls, const std::string& rec,
           int threads, const Config& config)
        : config_(config), detector_(det, threads), classifier_(cls, threads),
          recognizer_(rec, threads), characters_(recognizer_.characters()) {}

    std::string run(const cv::Mat& raw) {
        detector_.resetStats(); classifier_.resetStats(); recognizer_.resetStats();
        cv::Mat image = raw;
        int longest = std::max(image.cols, image.rows);
        if (longest > config_.i[0]) {
            double ratio = double(config_.i[0]) / longest;
            cv::resize(image, image, cv::Size(aligned(image.cols * ratio), aligned(image.rows * ratio)));
        }
        int shortest = std::min(image.cols, image.rows);
        if (shortest < config_.i[1]) {
            double ratio = double(config_.i[1]) / shortest;
            cv::resize(image, image, cv::Size(aligned(image.cols * ratio), aligned(image.rows * ratio)));
        }
        double scaleX = raw.cols / double(image.cols), scaleY = raw.rows / double(image.rows);
        int top = 0;
        if (image.rows <= config_.i[2] ||
            (config_.i[3] > 0 && image.cols / double(image.rows) > config_.i[3])) {
            int newHeight = std::max(image.cols / std::max(1, config_.i[3]), config_.i[2]) * 2;
            top = std::abs(newHeight - image.rows) / 2;
            cv::copyMakeBorder(image, image, top, top, 0, 0, cv::BORDER_CONSTANT, cv::Scalar(0,0,0));
        }
        auto boxes = detect(image);
        std::ostringstream json;
        json << '[';
        bool first = true;
        for (auto box : boxes) {
            // Only one text crop and its model output alive at a time.
            auto line = crop(image, box);
            classify(line);
            auto result = recognize(line);
            if (result.second < config_.f[3]) continue;
            if (!first) json << ',';
            first = false;
            json << '[' << '[';
            for (size_t n = 0; n < box.size(); ++n) {
                if (n) json << ',';
                json << '[' << std::clamp((box[n].x) * scaleX, 0., double(raw.cols))
                     << ',' << std::clamp((box[n].y - top) * scaleY, 0., double(raw.rows)) << ']';
            }
            json << "]," << quote(result.first) << ',' << result.second << ']';
        }
        json << ']';
        return "{\"results\":" + json.str() + ",\"models\":{\"det\":" + detector_.stats()
            + ",\"cls\":" + classifier_.stats() + ",\"rec\":" + recognizer_.stats() + "}}";
    }
};

std::string string(JNIEnv* env, jstring value) {
    const char* chars = env->GetStringUTFChars(value, nullptr);
    if (!chars) throw std::bad_alloc();
    std::string result(chars);
    env->ReleaseStringUTFChars(value, chars);
    return result;
}
void fail(JNIEnv* env, const std::exception& error, bool memory = false) {
    std::string message = error.what();
    memory = memory || message.find("allocate memory") != std::string::npos ||
             message.find("bad_alloc") != std::string::npos ||
             message.find("Insufficient memory") != std::string::npos;
    env->ThrowNew(env->FindClass(memory ? "java/lang/OutOfMemoryError" : "java/lang/IllegalStateException"),
                  message.c_str());
}
} // namespace

extern "C" JNIEXPORT jlong JNICALL
Java_com_lvjiang_app_NativeOcrBridge_create(JNIEnv* env, jobject, jstring det, jstring cls,
                                          jstring rec, jint threads, jintArray integers, jfloatArray floats) {
    try {
        if (env->GetArrayLength(integers) != 12 || env->GetArrayLength(floats) != 5)
            throw std::invalid_argument("Invalid native OCR configuration");
        Config config;
        env->GetIntArrayRegion(integers, 0, 12, config.i.data());
        env->GetFloatArrayRegion(floats, 0, 5, config.f.data());
        if (threads <= 0 || config.i[11] < 1024) throw std::invalid_argument("Invalid OCR resource budget");
        cv::setNumThreads(1);
        return reinterpret_cast<jlong>(new Engine(string(env,det), string(env,cls), string(env,rec), threads, config));
    } catch (const std::bad_alloc& error) { fail(env, error, true); }
      catch (const std::exception& error) { fail(env, error); }
    return 0;
}

extern "C" JNIEXPORT jbyteArray JNICALL
Java_com_lvjiang_app_NativeOcrBridge_run(JNIEnv* env, jobject, jlong handle, jbyteArray bytes,
                                       jint width, jint height) {
    try {
        if (!handle || width <= 0 || height <= 0 ||
            int64_t(width) * height * 3 != env->GetArrayLength(bytes))
            throw std::invalid_argument("Invalid BGR OCR input");
        // Release JNI input immediately after copying into one native byte image.
        cv::Mat image(height, width, CV_8UC3);
        env->GetByteArrayRegion(bytes, 0, env->GetArrayLength(bytes),
                               reinterpret_cast<jbyte*>(image.data));
        if (env->ExceptionCheck()) return nullptr;
        auto text = reinterpret_cast<Engine*>(handle)->run(image);
        auto result = env->NewByteArray(static_cast<jsize>(text.size()));
        if (result) env->SetByteArrayRegion(result, 0, static_cast<jsize>(text.size()),
                                           reinterpret_cast<const jbyte*>(text.data()));
        return result;
    } catch (const std::bad_alloc& error) { fail(env, error, true); }
      catch (const std::exception& error) { fail(env, error); }
    return nullptr;
}

extern "C" JNIEXPORT void JNICALL
Java_com_lvjiang_app_NativeOcrBridge_destroy(JNIEnv*, jobject, jlong handle) {
    delete reinterpret_cast<Engine*>(handle);
}
