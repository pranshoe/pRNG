import os, argparse, json

def generate_tree_code(tree, node_idx=0, indent="    "):
    left_child = tree["left_children"][node_idx]
    right_child = tree["right_children"][node_idx]

    if left_child == -1 and right_child == -1:
        weight = tree['base_weights'][node_idx]
        return f"{indent}return {weight:.8f}f;\n"

    feat_idx = tree['split_indices'][node_idx]
    threshold = tree['split_conditions'][node_idx]

    code = f"{indent}if (x[{feat_idx}] <= {threshold:.8f}f) {{\n"
    code += generate_tree_code(tree, left_child, indent=indent+"    ")
    code += f"{indent}}} else{{\n"
    code += generate_tree_code(tree, right_child, indent=indent+"    ")
    code += f"{indent}}}\n"
    return code

def xgbost_to_cpp(json_path, out_path, func_name, is_classifier=False):
    with open(json_path, 'r') as f:
        data = json.load(f)

    trees = data['learner']['gradient_booster']['model']['trees']
    feature_names = data['learner'].get("feature_names", [])
    num_trees = len(trees)

    cpp = f"""
    //Generated from {os.path.basename(json_path)}
    //Model: {num_trees} Dependence Trees

    #include <cmath>

    namespace model {{
    
    """

    for t_idx, tree in enumerate(trees):
        cpp += f"inline float tree_{t_idx}(const float* x) {{\n"
        cpp += generate_tree_code(tree, 0, indent="    ")
        cpp += "}\n\n"

    cpp += f"""//Main Function
    //Expected input: float array of size {len(feature_names)}
    inline float {func_name}(const float* x) {{
        float margin = 0.0f;
    """

    for t_idx in range(num_trees):
        cpp += f"    margin += tree_{t_idx}(x);\n"

    if is_classifier:
        cpp += """
            return 1.0f/(1.0f + std::exp(-margin));
        }
    """
    else:
        cpp += """
            return margin
        }
    """

    cpp += "\n}#endif\n"

    with open(out_path, 'w') as f:
        f.write(cpp)

    print(f"Converted {json_path} -> {out_path}")
    print(f"Features expected: {len(feature_names)}")
    print(f"Trees generated:   {num_trees}")


        
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Transform XGBoost Json to cpp")
    parser.add_argument("--json", type=str, required=True)
    parser.add_argument("--out", type=str, required=True)
    parser.add_argument("--func", type=str, required=True)
    parser.add_argument("--classifier", action="store_true")

    args=parser.parse_args()

    xgbost_to_cpp(args.json, args.out, args.func, args.classifier)
