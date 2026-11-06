#!/usr/bin/env python3
"""Compile pinned native language/request seams; no models, audio, network or GPU."""
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "language_patch", ROOT / "deploy/whisper-cpp-stt/patch-whisper-cpp-cuda.py")
PATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH)
FIXTURE = ROOT / "tests/fixtures/whisper-cpp-language.json"

PREAMBLE = r"""
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <map>
#include <string>
#include <vector>
#define WHISPER_API
#define WHISPER_LOG_ERROR(...) ((void)0)
#define WHISPER_LOG_INFO(...) ((void)0)
#define WHISPER_LOG_WARN(...) ((void)0)
using whisper_token = int;
struct whisper_context;
"""
STUBS = r"""
struct whisper_state {
    std::vector<int> result_all;
    int lang_id = 0;
    whisper_language_selection language_selection = {-1,-1,0,0};
};
struct whisper_context {
    whisper_state *state;
    float probs[39] = {};
    int detection_calls = 0, mel_calls = 0, detection_result = 38;
};
struct whisper_full_params {
    const char *language = "auto";
    bool detect_language = false, translate = false;
    int n_threads = 1;
    int offset_ms = 0, duration_ms = 30000;
    const int *language_candidates = nullptr;
    int n_language_candidates = 0;
};
int whisper_lang_max_id() { return 38; }
const char *whisper_lang_str(int id) {
    return id == 0 ? "en" : id == 26 ? "da" : id == 38 ? "cy" : nullptr;
}
int whisper_lang_id(const char *code) {
    if (!std::strcmp(code,"en") || !std::strcmp(code,"english")) return 0;
    if (!std::strcmp(code,"da") || !std::strcmp(code,"danish")) return 26;
    if (!std::strcmp(code,"cy") || !std::strcmp(code,"welsh")) return 38;
    return -1;
}
bool whisper_is_multilingual(whisper_context *) { return true; }
int whisper_token_sot(whisper_context *) { return 1; }
int whisper_token_lang(whisper_context *,int language) { return language + 2; }
int whisper_token_translate(whisper_context *) { return 50; }
int whisper_token_transcribe(whisper_context *) { return 51; }
int whisper_n_len_from_state(whisper_state *) { return 3000; }
int whisper_pcm_to_mel_with_state(whisper_context *ctx,whisper_state *,const float *,int,int) {
    ++ctx->mel_calls; return 0;
}
int whisper_lang_auto_detect_with_state(whisper_context *ctx,whisper_state *,int,int,float *probs) {
    ++ctx->detection_calls; std::copy(ctx->probs,ctx->probs+39,probs); return ctx->detection_result;
}
"""
SERVER = r"""
struct Value { std::string content; };
struct Request {
    std::multimap<std::string,Value> files;
    bool has_file(const char *key) const { return files.count(key)>0; }
    const Value &get_file_value(const char *key) const { return files.find(key)->second; }
};
struct Response {
    int status = 200;
    std::string body;
    void set_content(const std::string &text,const char *) { body=text; }
};
struct whisper_params {
    std::string language = "auto";
    bool detect_language = false;
    int n_processors = 1;
    std::vector<int> language_candidates;
};
void get_req_parameters(const Request &req,whisper_params &params) {
    if(req.has_file("language")) params.language=req.get_file_value("language").content;
}
"""
PROBES = r"""
int main(int argc,char **argv) {
    if(argc!=2) return 99;
    const std::string test=argv[1];
    whisper_state state; whisper_context ctx{&state}; whisper_full_params params;
    ctx.probs[38]=0.88f;ctx.probs[0]=0.11f;ctx.probs[26]=0.001f;
    const int ids[]={26,0};params.language_candidates=ids;params.n_language_candidates=2;
    auto run=[&]() { return whisper_full_with_state(&ctx,&state,params,nullptr,1); };
    if(test=="restricted") {
        if(run()!=0 || state.lang_id!=0 || ctx.detection_calls!=1) return 1;
        const auto selection=whisper_full_get_language_selection(&ctx);
        return selection.detected_id==38 && selection.selected_id==0 &&
            selection.detected_probability==0.88f && selection.selected_probability==0.11f ? 0:2;
    }
    if(test=="unrestricted") {
        params.language_candidates=nullptr;params.n_language_candidates=0;
        return run()==0 && state.lang_id==38 && ctx.detection_calls==1 &&
            state.language_selection.detected_id==-1 ? 0:1;
    }
    if(test=="explicit") {
        params.language="cy";
        if(run()!=0 || state.lang_id!=38 || ctx.detection_calls!=0) return 1;
        params.detect_language=true;
        return run()==0 && state.lang_id==38 && ctx.detection_calls==0 ? 0:2;
    }
    if(test=="ties") {
        ctx.probs[0]=ctx.probs[26]=0.5f;
        if(run()!=0 || state.lang_id!=0) return 1;
        const int reverse[]={0,26};params.language_candidates=reverse;
        return run()==0 && state.lang_id==0 ? 0:2;
    }
    if(test=="history") {
        if(run()!=0 || state.lang_id!=0) return 1;
        ctx.probs[26]=0.8f;ctx.probs[0]=0.1f;ctx.detection_result=26;
        if(run()!=0 || state.lang_id!=26) return 2;
        params.n_language_candidates=0;ctx.detection_result=38;
        if(run()!=0 || state.lang_id!=38 || state.language_selection.detected_id!=-1) return 3;
        params.n_language_candidates=2;params.language="en";
        return run()==0 && state.lang_id==0 && state.language_selection.detected_id==-1 && ctx.detection_calls==3 ? 0:4;
    }
    if(test=="short_explicit") {
        if(run()!=0 || state.lang_id!=0) return 1;
        params.language="da";params.duration_ms=50;
        return run()==0 && state.lang_id==26 && state.language_selection.detected_id==-1 &&
            state.language_selection.selected_id==26 && ctx.detection_calls==1 ? 0:2;
    }
    if(test=="invalid") {
        const int duplicate[]={0,0},unknown[]={100},unsupported[]={1};
        for(auto item:std::vector<std::pair<const int *,int>>{{nullptr,2},{ids,-1},{ids,17},{duplicate,2},{unknown,1},{unsupported,1}}) {
            params.language_candidates=item.first;params.n_language_candidates=item.second;
            if(run()!=-3 || ctx.detection_calls || ctx.mel_calls) return 1;
        }
        return 0;
    }
    if(test=="failure") {
        ctx.detection_result=-6;
        if(run()!=-3 || state.language_selection.detected_id!=-1) return 1;
        ctx.detection_result=38;ctx.probs[0]=NAN;
        return run()==-3 && state.language_selection.detected_id==-1 ? 0:2;
    }
    if(test=="parser") {
        std::vector<int> parsed;
        if(!parse_language_candidates("da,en",parsed) || parsed!=std::vector<int>({26,0})) return 1;
        for(const auto &csv:std::vector<std::string>{"","en,da","en,en","EN"," en","en ","en,","en,,da","english","zz","da;en",std::string(64,'a')}) {
            parsed={0};if(parse_language_candidates(csv,parsed) || !parsed.empty()) return 2;
        }
        return 0;
    }
    if(test=="request_isolation") {
        whisper_params defaults;Response response;
        Request first;first.files.emplace("language_candidates",Value{"da,en"});
        run_request(first,defaults,response,&ctx);
        if(response.status!=200 || state.lang_id!=0 || !defaults.language_candidates.empty()) return 1;
        Request next;run_request(next,defaults,response,&ctx);
        if(response.status!=200 || state.lang_id!=38 || state.language_selection.detected_id!=-1) return 2;
        first.files.emplace("language_candidates",Value{"da,en"});
        run_request(first,defaults,response,&ctx);
        if(response.status!=400 || ctx.detection_calls!=2) return 3;
        response.status=200;Request invalid;invalid.files.emplace("language_candidates",Value{"private-invalid-value"});
        run_request(invalid,defaults,response,&ctx);
        if(response.status!=400 || response.body.find("private")!=std::string::npos || ctx.detection_calls!=2) return 4;
        response.status=200;defaults.n_processors=2;Request parallel;parallel.files.emplace("language_candidates",Value{"da,en"});
        run_request(parallel,defaults,response,&ctx);
        return response.status==400 && ctx.detection_calls==2 ? 0:5;
    }
    return 98;
}
"""


def between(source, begin, end):
    return source.split(begin, 1)[1].split(end, 1)[0]


class LanguageCandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("c++")
        if not compiler:
            raise RuntimeError("Language candidate regression requires C++17")
        cls.original = json.loads(FIXTURE.read_text())["excerpts"]
        cls.patched = {name: PATCH.patch_language_candidates(source, name)
                       for name, source in cls.original.items()}
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.executables = {}
        header = cls.patched["include/whisper.h"].split("    WHISPER_API const char * whisper_version", 1)[0]
        for variant, files in (("upstream", cls.original), ("patched", cls.patched)):
            native, server = files["src/whisper.cpp"], files["examples/server/server.cpp"]
            helpers = between(native, "// BEGIN HELPERS\n", "static bool whisper_vad(")
            inference = between(native, "// BEGIN INFERENCE\n", "// END INFERENCE")
            inference += between(native, "// BEGIN EARLY RETURN\n", "// END EARLY RETURN")
            inference += between(native, "// BEGIN PROMPT\n", "// END PROMPT") + "return 0;\n}\n"
            getter = between(native, "// BEGIN GETTER\n", "// END GETTER")
            if variant == "upstream":
                getter += "whisper_language_selection whisper_full_get_language_selection(whisper_context *ctx) { return ctx->state->language_selection; }\n"
            parser = between(server, "// BEGIN PARSER\n", "void get_req_parameters(")
            if variant == "upstream":
                parser = "bool parse_language_candidates(const std::string &,std::vector<int>&) { return false; }\n"
            request = "void run_request(const Request &req,const whisper_params &default_params,Response &res,whisper_context *ctx) {\n"
            request += between(server, "// BEGIN REQUEST\n", "// END REQUEST")
            request += "whisper_full_params wparams;wparams.language=params.language.c_str();\n"
            request += between(server, "// BEGIN WIRE\n", "// END WIRE")
            request += "if(whisper_full_with_state(ctx,ctx->state,wparams,nullptr,1)!=0) res.status=500;\n}\n"
            cpp = Path(cls.directory.name) / (variant + ".cpp")
            cpp.write_text(PREAMBLE + header + STUBS + helpers + inference + getter + SERVER + parser + request + PROBES)
            executable = cpp.with_suffix("")
            result = subprocess.run([compiler, "-std=c++17", "-O2", str(cpp), "-o", str(executable)],
                                    capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise AssertionError(result.stderr)
            cls.executables[variant] = executable

    def probe(self, name, variant="patched"):
        result = subprocess.run([str(self.executables[variant]), name], capture_output=True, timeout=5)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        return result.returncode

    def test_original_native_detector_ignores_the_requested_language_subset(self):
        self.assertNotEqual(self.probe("restricted", "upstream"), 0)
        self.assertEqual(self.probe("restricted"), 0)

    def test_omitted_candidates_preserve_unrestricted_detector(self):
        for variant in ("upstream", "patched"):
            self.assertEqual(self.probe("unrestricted", variant), 0)

    def test_explicit_language_wins_without_an_extra_detector_pass(self):
        self.assertEqual(self.probe("explicit"), 0)

    def test_ties_are_stable_by_language_id_not_caller_order(self):
        self.assertEqual(self.probe("ties"), 0)

    def test_each_request_redetects_and_resets_diagnostics(self):
        self.assertEqual(self.probe("history"), 0)

    def test_short_explicit_request_does_not_report_previous_detected_language(self):
        self.assertEqual(self.probe("short_explicit"), 0)

    def test_native_invalid_ids_count_pointer_and_duplicates_fail_before_encoding(self):
        self.assertEqual(self.probe("invalid"), 0)

    def test_detector_failure_or_nonfinite_probability_never_becomes_a_selection(self):
        self.assertEqual(self.probe("failure"), 0)

    def test_csv_parser_accepts_only_bounded_canonical_codes(self):
        self.assertEqual(self.probe("parser"), 0)

    def test_actual_request_scope_does_not_reuse_candidates_after_valid_or_invalid_calls(self):
        self.assertEqual(self.probe("request_isolation"), 0)

    def test_exact_native_fixture_and_fail_closed_anchors(self):
        self.assertEqual(sha256(FIXTURE.read_bytes()).hexdigest(),
                         "ade0b435d9c27efbaa3d348a04b53a9589fc803eeebd2995313eecc7266ae203")
        for relative, patches in PATCH.LANGUAGE_PATCHES.items():
            for old, _new in patches:
                for source in (self.original[relative].replace(old, "", 1), self.original[relative] + old):
                    with self.assertRaisesRegex(ValueError, "language candidate anchor"):
                        PATCH.patch_language_candidates(source, relative)


if __name__ == "__main__":
    unittest.main(verbosity=2)
